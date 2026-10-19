"""Network policy, torrent data and tracker search normalization for Torrent Hub."""
from __future__ import annotations
import base64
import hashlib
import ipaddress
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
import httpx

HASH40 = re.compile(r'^[A-Fa-f0-9]{40}$')
HASH32 = re.compile(r'^[A-Za-z2-7]{32}$')
MAX_TORRENT_BYTES = 5 * 1024 * 1024


def sanitize_url(raw: str, *, private: bool = False) -> str:
    parsed = urllib.parse.urlsplit((raw or '').strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Требуется корректный HTTP(S) URL без встроенной авторизации')
    if private:
        try:
            ip = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            if parsed.hostname != 'localhost' and not parsed.hostname.endswith('.local'):
                raise ValueError('TorrServer должен находиться в локальной сети')
        else:
            if not (ip.is_private or ip.is_loopback):
                raise ValueError('TorrServer должен иметь локальный IP')
    return raw.strip().rstrip('/')


def btih(s: str) -> str:
    s = (s or '').strip()
    if HASH40.fullmatch(s):
        return s.lower()
    if HASH32.fullmatch(s):
        try:
            return base64.b32decode(s.upper()).hex()
        except ValueError:
            pass
    return ''


def magnet_hash(magnet: str) -> str:
    if not isinstance(magnet, str) or not magnet.startswith('magnet:?'):
        return ''
    for item in urllib.parse.parse_qs(urllib.parse.urlsplit(magnet).query).get('xt', []):
        if item.lower().startswith('urn:btih:'):
            return btih(item[9:])
    return ''


def magnet_for(hashcode: str, title: str = '', trackers: list[str] | None = None) -> str:
    hashcode = btih(hashcode)
    if not hashcode:
        return ''
    q = [('xt', 'urn:btih:'+hashcode)]
    if title:
        q.append(('dn', title[:300]))
    for tracker in (trackers or [])[:12]:
        if tracker.startswith(('udp://', 'http://', 'https://', 'wss://')):
            q.append(('tr', tracker))
    return 'magnet:?' + urllib.parse.urlencode(q)


def result_of(row: dict, source: str) -> dict:
    title = str(row.get('title') or row.get('name') or row.get('label') or 'Без названия')[:400]
    magnet = str(row.get('magnet') or row.get('magnetUrl') or '')
    download = str(row.get('downloadUrl') or row.get('download') or row.get('link') or '')
    if download.startswith('magnet:?') and not magnet:
        magnet = download
    hashcode = magnet_hash(magnet) or btih(str(row.get('infoHash') or row.get('infohash') or row.get('hash') or ''))
    if hashcode and not magnet:
        magnet = magnet_for(hashcode, title)
    def integer(*keys):
        for key in keys:
            try:
                if row.get(key) is not None: return max(0, int(row[key]))
            except (TypeError, ValueError):
                pass
        return 0
    return {'title': title, 'source': source,
            'tracker': str(row.get('tracker') or row.get('indexer') or source)[:120],
            'size': integer('size', 'sizeBytes'), 'seeders': integer('seeders','sid','seeds'),
            'leechers': integer('leechers','pir','peers'),
            'magnet': magnet if magnet_hash(magnet) else '', 'hash': hashcode,
            'url': str(row.get('infoUrl') or row.get('url') or '')[:1400],
            'download': download[:2500], 'indexer_id': row.get('indexerId'),
            'date': str(row.get('publishDate') or row.get('createTime') or row.get('date') or row.get('pubDate') or '')[:40]}


def merge_results(groups: list[list[dict]], limit=300) -> list[dict]:
    unique: dict[str, dict] = {}
    for group in groups:
        for x in group:
            x={**x,'source_names':x.get('source_names') or [x.get('source','')]}
            key = 'h:'+x['hash'] if x['hash'] else 's:'+re.sub(r'\W+', '', x['title'].lower())+':'+str(x['size'])
            if key not in unique:
                unique[key] = x
            else:
                old = unique[key]
                origin=list(dict.fromkeys(old['source_names']+x['source_names']))
                if (bool(x['magnet']), x['seeders']) > (bool(old['magnet']), old['seeders']):
                    unique[key] = {**x,'source_names':origin}
                else:
                    old['source_names']=origin
    return sorted(unique.values(), key=lambda a:(bool(a['magnet']),a['seeders'],a['size']), reverse=True)[:limit]


def proxy_address(cfg: dict) -> str:
    h = str(cfg.get('host','')).strip()
    port = int(cfg.get('port') or 0)
    if not h or not 1<=port<=65535 or any(c in h for c in '/:@?#'):
        raise ValueError('Неверные адрес или порт прокси')
    if cfg.get('type') not in ('http','socks5'):
        raise ValueError('Тип прокси должен быть HTTP или SOCKS5')
    auth = ''
    if cfg.get('username'):
        u = urllib.parse.quote(str(cfg['username']),safe='')
        p = urllib.parse.quote(str(cfg.get('password','')),safe='')
        auth = f'{u}:{p}@'
    return f'{cfg["type"]}://{auth}{h}:{port}'


def choose_routes(proxy: dict, mode: str) -> list[str]:
    if mode == 'inherit':
        mode = proxy.get('mode','proxy')
    if mode == 'proxy': return ['proxy']
    if mode == 'direct': return ['direct']
    if mode == 'auto': return ['direct','proxy']
    raise ValueError('Неизвестный режим сетевого маршрута')


def external_url_guard(url: str):
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password:
        raise ValueError('Источник должен иметь публичный HTTP(S) адрес')
    try: ip=ipaddress.ip_address(u.hostname)
    except ValueError: pass
    else:
        if not ip.is_global:raise ValueError('Запрещено обращение к частным или специальным адресам')


async def fetch(proxy_cfg: dict, mode: str, url: str, *, params=None, method='GET', json=None,
                timeout=18, headers=None, content=None, allow_local=False) -> httpx.Response:
    if not allow_local:external_url_guard(url)
    errors=[]
    for route in (['direct'] if allow_local else choose_routes(proxy_cfg, mode)):
        try:
            px=proxy_address(proxy_cfg) if route=='proxy' else None
            async with httpx.AsyncClient(proxy=px,timeout=httpx.Timeout(timeout,connect=7),trust_env=False,
                                         follow_redirects=False) as client:
                target=url
                verb=method
                req_params=params
                req_json=json
                for _ in range(5):
                    response=await client.request(verb,target,params=req_params,json=req_json,headers=headers,content=content)
                    if response.status_code not in (301,302,303,307,308):
                        response.raise_for_status()
                        return response
                    redirect=response.headers.get('location')
                    if not redirect:raise RuntimeError('Перенаправление без Location')
                    target=urllib.parse.urljoin(str(response.url),redirect)
                    if not allow_local:external_url_guard(target)
                    if response.status_code==303:
                        verb='GET';req_json=None
                    req_params=None
                raise RuntimeError('Слишком много перенаправлений')
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            errors.append(f'{route}: {str(exc)[:140]}')
    raise RuntimeError('; '.join(errors))


class BencodeError(ValueError): pass


def magnet_from_torrent(binary: bytes) -> str:
    """Hash the exact bencoded info bytes; parsing is bounded for hostile .torrent files."""
    if len(binary)>MAX_TORRENT_BYTES or not binary:
        raise BencodeError('Слишком большой или пустой torrent-файл')
    def parse(i, depth=0):
        if depth>40 or i>=len(binary): raise BencodeError('Некорректный bencode')
        t=binary[i:i+1]
        if t==b'i':
            end=binary.find(b'e',i+1)
            if end<0 or end-i>42: raise BencodeError('Некорректное число')
            try: return int(binary[i+1:end]),end+1,None
            except ValueError as exc: raise BencodeError('Некорректное число') from exc
        if t==b'l':
            arr=[];i+=1
            while i<len(binary) and binary[i:i+1]!=b'e':
                if len(arr)>20000: raise BencodeError('Слишком длинный список')
                v,i,_=parse(i,depth+1);arr.append(v)
            if i>=len(binary):raise BencodeError('Оборванный список')
            return arr,i+1,None
        if t==b'd':
            d={}; info_span=None;i+=1
            while i<len(binary) and binary[i:i+1]!=b'e':
                k,i,_=parse(i,depth+1)
                if not isinstance(k,bytes):raise BencodeError('Некорректный ключ')
                start=i;v,i,_=parse(i,depth+1)
                d[k]=v
                if k==b'info' and depth==0: info_span=(start,i)
            if i>=len(binary):raise BencodeError('Оборванный словарь')
            return d,i+1,info_span
        if t>=b'0' and t<=b'9':
            c=binary.find(b':',i)
            if c<0 or c-i>15: raise BencodeError('Некорректная строка')
            try: size=int(binary[i:c])
            except ValueError as exc: raise BencodeError('Некорректная строка') from exc
            if size<0 or c+1+size>len(binary):raise BencodeError('Некорректная длина')
            return binary[c+1:c+1+size],c+1+size,None
        raise BencodeError('Некорректный тип')
    data,end,span=parse(0)
    if end!=len(binary) or not isinstance(data,dict) or not span:
        raise BencodeError('Нет корневого info словаря')
    name=(data.get(b'info',{}).get(b'name.utf-8') or data.get(b'info',{}).get(b'name') or b'').decode('utf8','replace')[:180]
    trackers=[]
    for tr in [data.get(b'announce')]+[v for group in data.get(b'announce-list',[]) if isinstance(group,list) for v in group]:
        if isinstance(tr,bytes): trackers.append(tr.decode('utf8','replace'))
    return magnet_for(hashlib.sha1(binary[span[0]:span[1]]).hexdigest(),name,trackers)


def status(ok: bool, message: str, latency_ms: int=0, **extra) -> dict:
    return {'ok':ok,'message':message[:300], 'latency_ms':latency_ms,
            'checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),**extra}
