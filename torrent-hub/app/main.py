"""Torrent Hub backend — HA ingress, bundled indexer engine, checks and TorrServer control."""
from __future__ import annotations
import asyncio
import copy
import json
import logging
import os
import pathlib
import shutil
import secrets
import time
import re
import uuid
import hashlib
import hmac
import xml.etree.ElementTree as ET
from collections import deque
from contextlib import asynccontextmanager
from urllib.parse import quote, urlsplit

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from direct import TRACKERS, search_tracker
from core import (BencodeError, btih, choose_routes, fetch, magnet_for, magnet_from_torrent,
                  magnet_hash, merge_results, proxy_address, result_of, sanitize_url, status)

BASE = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.environ.get('TORRENT_HUB_DATA','/data/torrent-hub'))
P_DATA = pathlib.Path(os.environ.get('PROWLARR_DATA','/data/prowlarr'))
STATE = DATA/'state.json'
MEDIA_DIR=pathlib.Path(os.environ.get('TORRENT_HUB_MEDIA_DIR','/media/TorrentHub'))
P_URL='http://127.0.0.1:9696'
log=logging.getLogger('torrent_hub')
logging.basicConfig(level=logging.INFO)
MUTEX=asyncio.Lock()
CHECKS={}
EVENTS=deque(maxlen=200)
SEARCH_CACHE={}
JOBS={}
JOB_LOCK=asyncio.Lock()
AUTO_INSTALL_DONE=False
DEFAULT_TRACKERS=('rutor','noname-club','megapeer')


def defaults():
    return {'version':3,'proxy': {'mode':'direct','type':'socks5','host':'','port':0,'username':'','password':''},
            'sources':[
                {'id':'jr-maxvol','name':'jr.maxvol.pro','url':'https://jr.maxvol.pro','enabled':True,'group':'public','route':'inherit','kind':'jacred'},
                {'id':'jac-black','name':'jac.black','url':'https://jac.black','enabled':True,'group':'public','route':'inherit','kind':'jacred'},
                {'id':'ru-jac-black','name':'ru.jac.black','url':'https://ru.jac.black','enabled':True,'group':'public','route':'inherit','kind':'jacred'},
                {'id':'knaben','name':'Knaben API','url':'https://api.knaben.org/v1','enabled':True,'group':'public','route':'inherit','kind':'knaben'}],
            'servers':[
                {'id':'lg','name':'LG — приставка','url':'http://192.168.1.148:8090','username':'','password':''},
                {'id':'ha','name':'Home Assistant','url':'http://192.168.1.100:8090','username':'','password':''}],
            'search':{'enable_prowlarr':False,'check_interval':900,'max_results':300,'remember_history':False,'include_public':True,'cache_minutes':10,'cache_max':350},
            'remote':{'username':'admin','password_salt':'','password_hash':''},
            'smart':{'min_size':5,'max_size':10,'min_seeders':10,'quality':'all','strict':False,'profile':'balanced'},
            'direct_trackers':[{'id':k,'kind':k,'name':v['name'],'url':v['url'],'enabled':True,'route':'inherit'} for k,v in TRACKERS.items()]}


def load():
    if STATE.exists():
        value=json.loads(STATE.read_text('utf8'))
        if isinstance(value,dict) and 'proxy' in value and 'servers' in value:
            if value.get('version',1)<2:
                # Earlier releases disabled every aggregator by default, making the
                # 'include public' checkbox a no-op for most installations.
                initial={x['id'] for x in defaults()['sources']}
                for src in value.get('sources',[]):
                    if src.get('id') in initial: src['enabled']=True
                value.setdefault('search',{})['include_public']=True
                value['version']=2
            value.setdefault('search',{}).update({'enable_prowlarr':False})
            value['search'].setdefault('cache_minutes',10)
            value['search'].setdefault('cache_max',350)
            value.setdefault('remote',defaults()['remote'])
            value.setdefault('smart',defaults()['smart'])
            value.setdefault('direct_trackers',defaults()['direct_trackers'])
            value['version']=3
            return value
    return defaults()

config=load()


def save():
    DATA.mkdir(parents=True,exist_ok=True)
    f=DATA/('.'+uuid.uuid4().hex+'.tmp')
    f.write_text(json.dumps(config,indent=2,ensure_ascii=False),encoding='utf8')
    os.chmod(f,0o600)
    os.replace(f,STATE)


def publish(message: str):
    EVENTS.appendleft({'at':time.strftime('%Y-%m-%d %H:%M:%S'),'message':message[:300]})


def redacted():
    out=copy.deepcopy(config)
    out['proxy']['password_set']=bool(out['proxy'].pop('password',''))
    out.setdefault('remote',defaults()['remote'])
    out['remote']['password_set']=bool(out['remote'].pop('password_hash',''))
    out['remote'].pop('password_salt',None)
    for source in out['sources']:
        source['api_key_set']=bool(source.pop('api_key',''))
    for server in out['servers']:
        server['password_set']=bool(server.pop('password',''))
    return out


def prowlarr_key():
    try:
        root=ET.fromstring((P_DATA/'config.xml').read_text('utf8'))
        return root.findtext('ApiKey','').strip()
    except (FileNotFoundError,ET.ParseError,PermissionError):
        return ''


async def prowlarr(method: str, path: str, **kwargs):
    key=prowlarr_key()
    if not key:raise RuntimeError('Prowlarr ещё запускается: отсутствует API-ключ')
    async with httpx.AsyncClient(trust_env=False,timeout=httpx.Timeout(45,connect=5)) as client:
        r=await client.request(method,P_URL+path,headers={'X-Api-Key':key},**kwargs)
        if r.status_code>=400:
            raise RuntimeError(f'Prowlarr HTTP {r.status_code}: {r.text[:260]}')
        return r


async def check_proxy(proxy:dict) -> dict:
    start=time.monotonic()
    try:
        uri=proxy_address(proxy)
        async with httpx.AsyncClient(proxy=uri,timeout=httpx.Timeout(13,connect=5),trust_env=False) as client:
            resp=await client.get('https://api.ipify.org?format=json')
            resp.raise_for_status()
            data=resp.json()
        return status(True,'Внешний IP через прокси: '+str(data.get('ip','?')),round((time.monotonic()-start)*1000),external_ip=data.get('ip'))
    except Exception as exc:
        return status(False,'Ошибка подключения/авторизации: '+str(exc))


async def check_server(server:dict) -> dict:
    start=time.monotonic()
    try:
        url=sanitize_url(server['url'],private=True)
        auth=(server.get('username',''),server.get('password','')) if server.get('username') else None
        async with httpx.AsyncClient(timeout=7,trust_env=False,auth=auth) as client:
            resp=await client.get(url+'/echo')
            resp.raise_for_status()
            version=resp.text.strip().strip('"')[:70]
        return status(True, f'TorrServer отвечает ({version})',round((time.monotonic()-start)*1000))
    except Exception as exc:
        return status(False,'Нет ответа TorrServer: '+str(exc))


async def search_external(src:dict,proxy:dict,query:str) -> list[dict]:
    """Search self-hosted or public APIs; a reachable homepage is not considered search."""
    url=sanitize_url(src['url'])
    kind=src.get('kind','jacred')
    route=src.get('route','inherit')
    key=src.get('api_key','')
    if kind=='jacred':
        endpoint=url if url.endswith('/api/v1.0/torrents') else url+'/api/v1.0/torrents'
        params={'search':query}
        if key:params['apikey']=key
        r=await fetch(proxy,route,endpoint,params=params,timeout=22)
        obj=r.json()
        if isinstance(obj,dict):obj=next((obj[k] for k in ('results','torrents','data','items') if isinstance(obj.get(k),list)),None)
    elif kind=='knaben':
        endpoint=url if url.endswith('/api/v1') or url.endswith('/v1') else url.rstrip('/')
        r=await fetch(proxy,route,endpoint,method='POST',json={'query':query,'size':100,'hide_unsafe':True},timeout=22)
        response=r.json()
        obj=response.get('hits',response.get('results')) if isinstance(response,dict) else response
        if isinstance(obj,list):
            obj=[{'title':row.get('title'), 'infoHash':row.get('hash'),
                  'magnetUrl':row.get('magnetUrl'), 'size':row.get('bytes'),
                  'seeders':row.get('seeders'), 'leechers':row.get('peers'),
                  'url':row.get('details'), 'tracker':row.get('cachedOrigin'),'date':row.get('date')}
                 for row in obj if isinstance(row,dict)]
    elif kind=='torznab':
        endpoint=url if url.endswith('/api') else url+'/api'
        params={'t':'search','q':query,'limit':100}
        if key:params['apikey']=key
        r=await fetch(proxy,route,endpoint,params=params,timeout=22)
        if len(r.content)>3_000_000:raise ValueError('Слишком большой ответ Torznab')
        root=ET.fromstring(r.content)
        if root.find('.//error') is not None:
            raise ValueError('Torznab сообщил об ошибке авторизации или запроса')
        obj=[]
        for item in root.findall('.//item')[:100]:
            fields={'title':item.findtext('title',''), 'url':item.findtext('guid','')}
            encl=item.find('enclosure')
            if encl is not None:
                fields['downloadUrl']=encl.attrib.get('url','')
                fields['size']=encl.attrib.get('length',0)
            fields['downloadUrl']=fields.get('downloadUrl') or item.findtext('link','')
            for node in item.iter():
                if node.tag.rsplit('}',1)[-1]!='attr':continue
                k=node.attrib.get('name','').lower();v=node.attrib.get('value','')
                if k in ('seeders','peers','leechers','size'):fields[k]=v
                if k in ('infohash','hash'):fields['infoHash']=v
                if k=='magneturl':fields['magnetUrl']=v
            obj.append(fields)
    else:raise ValueError('Неизвестный тип API')
    if not isinstance(obj,list):raise ValueError('API не вернул список поисковых результатов')
    out=[]
    for row in obj[:250]:
        if isinstance(row,dict):
            found=result_of(row,src['name'])
            found['route']=route
            found['source_id']=src['id']
            out.append(found)
    return out


async def check_jacred(source:dict,proxy:dict,probe=True) -> dict:
    start=time.monotonic()
    try:
        obj=await search_external(source,proxy,'Интерстеллар' if probe else 'ubuntu')
        delay=round((time.monotonic()-start)*1000)
        return status(True,('Найдены реальные раздачи: ' if obj else 'API отвечает, но тестовый поиск пустой: ')+str(len(obj)),delay,count=len(obj),state='ok' if obj else 'empty')
    except Exception as exc:
        return status(False,'Ошибка поискового API: '+str(exc),round((time.monotonic()-start)*1000),state='error')


async def _engine_source(query:str,selected_ids:list[int]|None=None):
    await verify_engine_route()
    params={'query':query}
    if selected_ids:
        params['indexerIds']=selected_ids
    rows=(await prowlarr('GET','/api/v1/search',params=params)).json()
    if isinstance(rows,dict):rows=rows.get('releases',rows.get('items',[]))
    if not isinstance(rows,list):raise ValueError('Prowlarr вернул неожиданный формат результатов')
    return [dict(result_of(row,'Prowlarr'), source_id='prowlarr', route='inherit') for row in rows[:500]]


async def install_default_indexers():
    """Try to provision public trackers; network errors never prevent Hub startup."""
    global AUTO_INSTALL_DONE
    if AUTO_INSTALL_DONE:return
    try:
        installed=(await prowlarr('GET','/api/v1/indexer')).json()
        schemas=(await prowlarr('GET','/api/v1/indexer/schema')).json()
        defined={re.sub('[^a-z0-9]','',str(x.get('definitionName') or x.get('name','')).lower()):x for x in schemas if x.get('protocol')=='torrent'}
        existing={re.sub('[^a-z0-9]','',str(x.get('name','')).lower()) for x in installed}
        unresolved=[]
        for ident in DEFAULT_TRACKERS:
            key=ident.replace('-','')
            src=defined.get(key)
            if src is None:
                publish('Определение '+ident+' не найдено; проверим позже')
                unresolved.append(ident)
                continue
            if re.sub('[^a-z0-9]','',str(src.get('name','')).lower()) in existing:continue
            try:
                model=copy.deepcopy(src);model['enable']=True
                await prowlarr('POST','/api/v1/indexer',params={'forceSave':'true'},json=model)
                publish('Автоматически добавлен трекер '+str(src.get('name',ident)))
            except Exception as exc:
                unresolved.append(ident)
                publish('Не удалось добавить '+ident+': '+str(exc)[:110])
        AUTO_INSTALL_DONE=not bool(unresolved)
    except Exception as exc:publish('Автоматическая настройка трекеров: '+str(exc)[:130])


async def verify_engine_route():
    """Fail closed: a direct Prowlarr HTTP call cannot silently bypass the requested proxy."""
    p=config['proxy']
    if p['mode']=='auto':
        raise RuntimeError('Prowlarr не поддерживает авто-переключение сетевого маршрута; выберите «прокси» либо «напрямую»')
    settings=(await prowlarr('GET','/api/v1/config/host')).json()
    enabled=bool(settings.get('proxyEnabled'))
    if p['mode']=='proxy':
        if not enabled or str(settings.get('proxyHostname',''))!=str(p['host']) or int(settings.get('proxyPort',0))!=int(p['port']) or str(settings.get('proxyType','')).lower()!=str(p['type']).lower():
            raise RuntimeError('Prowlarr не подключён к заданному прокси. На вкладке «Прокси» нажмите «Сохранить и применить». Прямой поиск заблокирован.')
    else:
        if enabled:raise RuntimeError('Prowlarr пока настроен на прокси. Примените режим «Напрямую» на вкладке «Прокси».')


async def check_prowlarr() -> dict:
    start=time.monotonic()
    try:
        results=(await prowlarr('GET','/api/v1/indexer')).json()
        count=sum(bool(i.get('enable')) for i in results)
        return status(count>0, f'Prowlarr работает, активных индексаторов: {count}',round((time.monotonic()-start)*1000),count=count)
    except Exception as exc:
        return status(False,'Prowlarr: '+str(exc))


async def run_check(category='all',entry=None,draft=None):
    proxy=config['proxy'].copy()
    if category=='proxy':
        value=await check_proxy({**proxy,**(draft or {}),'password':(draft or {}).get('password') or proxy.get('password','')})
    elif category in ('prowlarr','direct'):
        enabled=[t for t in config['direct_trackers'] if t.get('enabled')]
        value=status(bool(enabled),f'Прямой поиск: включено {len(enabled)} трекера(ов)',0,count=len(enabled))
    elif category=='server':
        value=await check_server(draft or next(x for x in config['servers'] if x['id']==entry))
    elif category=='tracker':
        item=draft or next(x for x in config['direct_trackers'] if x['id']==entry)
        started=time.monotonic()
        try:
            rows=await search_tracker(item,proxy,'Интерстеллар')
            value=status(True,f'Прямой поиск: {len(rows)} раздач',round((time.monotonic()-started)*1000),count=len(rows),state='ok' if rows else 'empty')
        except Exception as exc:
            value=status(False,'Прямой поиск: '+str(exc)[:180],round((time.monotonic()-started)*1000),state='error')
    elif category=='source':
        item=draft or next(x for x in config['sources'] if x['id']==entry)
        if draft and not item.get('api_key'):
            item={**item,'api_key':next((x.get('api_key','') for x in config['sources'] if x['id']==item.get('id')),'')}
        value=await check_jacred(item,proxy)
    elif category=='all':
        async def task(category,name,item=None):
            try:
                r=await run_check(category,name,item)
                CHECKS[category+(':'+name if name else '')]=r
            except Exception as exc:
                CHECKS[category+(':'+name if name else '')]=status(False,str(exc))
        await asyncio.gather(task('proxy',None),task('direct',None),
            *(task('tracker',t['id']) for t in config['direct_trackers'] if t.get('enabled')),
            *(task('server',s['id']) for s in config['servers']),
            *(task('source',s['id']) for s in config['sources'] if s.get('enabled')))
        return CHECKS
    else: raise ValueError('Некорректная категория')
    if draft is None: CHECKS[category+(':'+entry if entry else '')]=value
    return value


async def initialise_indexers():
    """Retry slow first-boot indexer provisioning independently of UI/health checks."""
    proxy_applied=False
    while True:
        if prowlarr_key():
            if not proxy_applied and config['proxy']['mode'] in ('proxy','direct'):
                try:
                    await apply_engine_proxy()
                    proxy_applied=True
                except Exception as exc:
                    publish('Настройка прокси поисковика: '+str(exc)[:130])
            if not AUTO_INSTALL_DONE:
                await install_default_indexers()
            if proxy_applied and AUTO_INSTALL_DONE:
                return
        await asyncio.sleep(75)


def purge_expired():
    now=time.monotonic()
    ttl=max(1,int(config['search'].get('cache_minutes',10)))*60
    for key,(created,_) in list(SEARCH_CACHE.items()):
        if now-created>ttl:SEARCH_CACHE.pop(key,None)
    for key,job in list(JOBS.items()):
        if now-job['created']>ttl:JOBS.pop(key,None)


async def cache_sweeper():
    while True:
        purge_expired()
        await asyncio.sleep(60)


async def monitoring():
    while True:
        try:
            await run_check('all')
        except Exception as exc:
            log.error('check cycle: %s',exc)
        await asyncio.sleep(max(60,int(config['search'].get('check_interval',300))))


@asynccontextmanager
async def lifespan(_app):
    tasks=[asyncio.create_task(monitoring()),asyncio.create_task(cache_sweeper())]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:await task
        except asyncio.CancelledError:pass

app=FastAPI(title='Torrent Hub',docs_url=None,redoc_url=None,lifespan=lifespan)


class Search(BaseModel):
    query:str=Field(min_length=2,max_length=160)
    include_public:bool=True
    selected_sources:list[str]|None=None
    selected_indexers:list[int]|None=None

class Send(BaseModel):
    server_ids:list[str]
    id:str

class Resolve(BaseModel):
    id:str

class Config(BaseModel):
    config:dict

class Test(BaseModel):
    category:str
    id:str|None=None
    draft:dict|None=None


@app.get('/')
async def index(): return FileResponse(BASE/'static'/'index.html')

@app.get('/icon.png')
async def icon():
    return FileResponse(BASE/'static'/'icon.png',media_type='image/png')

@app.get('/healthz')
async def health(): return {'ok':True,'component':'torrent-hub'}

@app.get('/api/status')
async def overall(): return {'checks':CHECKS,'events':list(EVENTS)[:50], 'engine_key_ready':True,'mode':'live','cache':cache_stats()}

@app.get('/api/config')
async def settings(): return redacted()


def update_config(old,body):
    """Validate into a new object; omitted passwords preserve existing credentials."""
    if not isinstance(body,dict):raise ValueError('Ожидается объект настроек')
    out=copy.deepcopy(old)
    if 'proxy' in body:
        p=body['proxy']
        if not isinstance(p,dict):raise ValueError('Неверные настройки прокси')
        for key in ('host','port','mode','type','username'):
            if key in p:out['proxy'][key]=p[key]
        if p.get('password'):out['proxy']['password']=str(p['password'])
        if p.get('clear_password'):out['proxy']['password']=''
        if out['proxy']['mode'] not in ('direct','proxy','auto'):raise ValueError('Неверный режим прокси')
        if out['proxy']['mode']!='direct':proxy_address(out['proxy'])
    for collection in ('sources','servers'):
        if collection not in body:continue
        arr=body[collection]
        if not isinstance(arr,list) or len(arr)>80:raise ValueError('Слишком много записей')
        previous={x['id']:x for x in old[collection]}
        seen=set();clean=[]
        for item in arr:
            if not isinstance(item,dict):raise ValueError('Неверная запись')
            uid=str(item.get('id') or uuid.uuid4().hex[:12])[:50]
            if uid in seen:raise ValueError('Повторяющийся ID');
            seen.add(uid)
            name=str(item.get('name') or '').strip()[:90]
            if not name:raise ValueError('Требуется название')
            row={'id':uid,'name':name,'url':sanitize_url(item.get('url',''),private=(collection=='servers'))}
            if collection=='sources':
                row.update({'enabled':bool(item.get('enabled',False)),
                    'group':item.get('group','public'),'route':item.get('route','inherit'),
                    'kind':item.get('kind','jacred'),
                    'api_key':str(item.get('api_key') or previous.get(uid,{}).get('api_key',''))[:256]})
                if item.get('clear_api_key'):row['api_key']=''
                if row['group'] not in ('main','public') or row['route'] not in ('inherit','direct','proxy','auto') or row['kind'] not in ('jacred','knaben','torznab'):
                    raise ValueError('Неверный тип поискового источника')
            else:
                row['username']=str(item.get('username',''))[:120]
                row['password']=str(item.get('password') or previous.get(uid,{}).get('password',''))[:200]
                if item.get('clear_password'):row['password']=''
            clean.append(row)
        out[collection]=clean
    if 'direct_trackers' in body:
        candidates=body['direct_trackers']
        if not isinstance(candidates,list) or len(candidates)>12:raise ValueError('Слишком много трекеров')
        known=set();arr=[]
        for item in candidates:
            kind=str(item.get('kind',''))
            ident=str(item.get('id',''))
            if kind not in (*TRACKERS.keys(),'custom') or not re.fullmatch(r'[a-zA-Z0-9_-]{1,40}',ident) or ident in known:
                raise ValueError('Неподдерживаемый или повторный трекер')
            if kind!='custom' and ident!=kind:raise ValueError('Неверный ID стандартного трекера')
            known.add(ident)
            name=TRACKERS[kind]['name'] if kind in TRACKERS else str(item.get('name','')).strip()[:70]
            if not name:raise ValueError('Укажи название трекера')
            record={'id':ident,'kind':kind,'name':name,
                    'url':sanitize_url(item.get('url',TRACKERS.get(kind,{}).get('url',''))),
                    'enabled':bool(item.get('enabled',True)),
                    'route':item.get('route','inherit')}
            if record['route'] not in ('inherit','proxy','direct','auto'):raise ValueError('Неверный маршрут')
            if kind=='custom':
                adapter=item.get('adapter',{})
                if not isinstance(adapter,dict):raise ValueError('Неверное описание CSS-адаптера')
                required=['path','row','title']
                for key in required:
                    if not isinstance(adapter.get(key),str) or not 1<=len(adapter[key])<=180:
                        raise ValueError('Не заполнен параметр адаптера: '+key)
                if not adapter['path'].startswith('/') or adapter['path'].startswith('//') or '{query}' not in adapter['path']:
                    raise ValueError('Путь поиска должен начинаться с / и содержать {query}')
                allowed=['path','row','title','details','magnet','download','size','seeders','date','encoding']
                record['adapter']={}
                for key in allowed:
                    val=str(adapter.get(key,'')).strip()
                    if len(val)>180:raise ValueError('Слишком длинное поле адаптера')
                    record['adapter'][key]=val
                if record['adapter']['encoding'] not in ('','utf-8','cp1251'):
                    raise ValueError('Поддерживаются UTF-8 и Windows-1251')
            arr.append(record)
        out['direct_trackers']=arr
    if 'remote' in body:
        access=body['remote']
        if not isinstance(access,dict):raise ValueError('Неправильные настройки доступа')
        username=str(access.get('username',out['remote'].get('username','admin'))).strip()
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,40}',username):raise ValueError('Логин: 3–40 латинских символов, цифр, точек или дефисов')
        out['remote']['username']=username
        if access.get('password'):
            password=str(access['password'])
            if len(password)<12 or len(password)>128:raise ValueError('Пароль внешнего доступа: 12–128 символов')
            salt=secrets.token_bytes(16)
            out['remote']['password_salt']=salt.hex()
            out['remote']['password_hash']=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32).hex()
        if access.get('disable'):
            out['remote']['password_salt']=''
            out['remote']['password_hash']=''
    if 'smart' in body:
        prefs=body['smart']
        if not isinstance(prefs,dict):raise ValueError('Неверные параметры умного поиска')
        min_size=float(prefs.get('min_size',out['smart']['min_size']))
        max_size=float(prefs.get('max_size',out['smart']['max_size']))
        min_seeders=int(prefs.get('min_seeders',out['smart']['min_seeders']))
        quality=str(prefs.get('quality',out['smart']['quality']))
        profile=str(prefs.get('profile',out['smart'].get('profile','balanced')))
        if not 0<=min_size<=max_size<=200 or not 0<=min_seeders<=2000:raise ValueError('Некорректный диапазон размера или сидов')
        if quality not in ('all','1080','2160','720'):raise ValueError('Неизвестное качество')
        if profile not in ('balanced','compact','maximum','custom'):raise ValueError('Неизвестный профиль')
        out['smart']={'min_size':min_size,'max_size':max_size,'min_seeders':min_seeders,
                      'quality':quality,'strict':bool(prefs.get('strict',out['smart']['strict'])),'profile':profile}
    if 'search' in body:
        s=body['search'];out['search']['enable_prowlarr']=False
        for key,minimum,maximum in (('cache_minutes',1,60),('cache_max',50,1000)):
            if key in s:
                value=int(s[key])
                if not minimum<=value<=maximum:raise ValueError('Неверный лимит кеша')
                out['search'][key]=value
        if 'check_interval' in s:
            seconds=int(s['check_interval'])
            if seconds<60 or seconds>3600:raise ValueError('Интервал проверок 60–3600 секунд')
            out['search']['check_interval']=seconds
    return out


@app.get('/api/direct-trackers')
async def direct_trackers():
    return {'trackers':config['direct_trackers'],'cache':cache_stats()}


@app.put('/api/config')
async def set_settings(payload:Config):
    global config
    async with MUTEX:
        try: updated=update_config(config,payload.config)
        except (ValueError,KeyError,TypeError) as exc:raise HTTPException(400,str(exc))
        config=updated;save()
        publish('Настройки сохранены')
    return redacted()


@app.post('/api/test')
async def test(payload:Test):
    try:return {'result':await run_check(payload.category,payload.id,payload.draft)}
    except (KeyError,StopIteration,ValueError) as exc:raise HTTPException(400,str(exc))


@app.post('/api/test-all')
async def testall():
    await run_check('all')
    return {'checks':CHECKS}


@app.get('/api/indexers')
async def indexers():
    try:
        found=(await prowlarr('GET','/api/v1/indexer')).json()
        errors=(await prowlarr('GET','/api/v1/indexerstatus')).json()
        return {'indexers':[{'id':i['id'],'name':i.get('name'),'enable':i.get('enable'),
                             'protocol':i.get('protocol'),'tags':i.get('tags',[])} for i in found],
                'errors':errors}
    except Exception as exc:raise HTTPException(503,str(exc))


@app.post('/api/indexers/test-all')
async def test_all_indexers():
    # Unlike 'is configured' this invokes the actual live checks in Prowlarr.
    try:
        returned=await prowlarr('POST','/api/v1/indexer/testall')
        try:response=returned.json()
        except ValueError:response=[]
        publish('Реальная проверка всех индексаторов завершена')
        return {'checks':response}
    except Exception as exc:raise HTTPException(502,'Ошибка реальной проверки индексаторов: '+str(exc))


@app.get('/api/indexer-catalog')
async def catalog():
    try:
        schemas=(await prowlarr('GET','/api/v1/indexer/schema')).json()
        return [{'definitionName':s.get('definitionName'),'name':s.get('name'),
                 'privacy':s.get('privacy'),'protocol':s.get('protocol'),
                 'description':s.get('description')} for s in schemas if s.get('protocol')=='torrent']
    except Exception as exc:raise HTTPException(503,str(exc))


class IndexerAdd(BaseModel):
    name:str=Field(min_length=2,max_length=80)
    fields:dict={}


@app.get('/api/indexer-schema/{name}')
async def indexer_schema(name:str):
    try:
        models=(await prowlarr('GET','/api/v1/indexer/schema')).json()
        model=next((x for x in models if x.get('definitionName','').lower()==name.lower() or x.get('name','').lower()==name.lower()),None)
        if not model:raise HTTPException(404,'Трекер отсутствует в каталоге')
        return {'name':model.get('name'),'definitionName':model.get('definitionName'),
                'fields':[{'name':x.get('name'),'label':x.get('label'),'value':x.get('value'),
                           'type':x.get('type'),'options':x.get('selectProviderOptions') or x.get('options') or [],
                           'advanced':x.get('advanced',False)} for x in model.get('fields',[]) if x.get('name')]}
    except HTTPException:raise
    except Exception as exc:raise HTTPException(502,str(exc))


@app.post('/api/indexers')
async def add_indexer(payload:IndexerAdd):
    try:
        schemas=(await prowlarr('GET','/api/v1/indexer/schema')).json()
        candidates=[s for s in schemas if s.get('protocol')=='torrent' and
                    payload.name.lower() in [str(s.get('definitionName','')).lower(),str(s.get('name','')).lower()]]
        if not candidates:raise HTTPException(404,'Индексатор не найден в каталоге Prowlarr')
        model=copy.deepcopy(candidates[0]);model['enable']=True
        for field in model.get('fields',[]):
            if field.get('name') in payload.fields:
                field['value']=payload.fields[field['name']]
        # Bypass initial live test; availability monitor will show if tracker is unavailable.
        r=await prowlarr('POST','/api/v1/indexer',params={'forceSave':'true'},json=model)
        publish('Добавлен индексатор: '+payload.name)
        return {'ok':True,'name':r.json().get('name')}
    except HTTPException:raise
    except Exception as exc:raise HTTPException(502,str(exc))


@app.delete('/api/indexers/{indexer_id}')
async def delete_indexer(indexer_id:int):
    try:await prowlarr('DELETE',f'/api/v1/indexer/{indexer_id}');return {'ok':True}
    except Exception as exc:raise HTTPException(502,str(exc))


async def apply_engine_proxy():
    """Keep Prowlarr General proxy in sync, never quietly skip a failed PUT."""
    proxy=config['proxy']
    if proxy['mode']=='auto':
        raise HTTPException(400,'Автопереключение режима поддерживают JacRed-источники; для Prowlarr выберите «прокси» или «напрямую»')
    try:
        host=(await prowlarr('GET','/api/v1/config/host')).json()
        host.update({'proxyEnabled':proxy['mode']=='proxy',
                     'proxyType':proxy['type'],'proxyHostname':proxy['host'],
                     'proxyPort':int(proxy['port']), 'proxyUsername':proxy['username'],
                     'proxyPassword':proxy['password'],'proxyBypassLocalAddresses':True,
                     'proxyBypassFilter':'192.168.1.*,127.0.0.1,localhost'})
        # Prowlarr validates passwordConfirmation against password in unrelated auth settings.
        host['passwordConfirmation']=host.get('password','')
        ident=host.get('id')
        if ident is None:raise ValueError('Prowlarr не вернул идентификатор настроек')
        await prowlarr('PUT',f'/api/v1/config/host/{ident}',json=host)
        publish('Маршрутизация Prowlarr обновлена')
        return {'ok':True,'mode':proxy['mode']}
    except Exception as exc:raise HTTPException(502,'Не удалось применить прокси в Prowlarr: '+str(exc))


@app.post('/api/engine-proxy')
async def configure_engine_proxy():
    return await apply_engine_proxy()


async def provider_plan(payload:Search):
    cfg=copy.deepcopy(config)
    plan=[]
    for tracker in cfg['direct_trackers']:
        if not tracker.get('enabled'):continue
        ident='direct:'+tracker['id']
        if payload.selected_sources is not None and ident not in payload.selected_sources:continue
        async def live(item=tracker):return await search_tracker(item,cfg['proxy'],payload.query.strip())
        plan.append((ident,tracker['name']+' — напрямую',live))
    for src in cfg['sources']:
        if not src['enabled'] or (src['group']=='public' and not payload.include_public):continue
        if payload.selected_sources is not None and src['id'] not in payload.selected_sources:continue
        async def search_one(s=src):return await search_external(s,cfg['proxy'],payload.query.strip())
        plan.append((src['id'],src['name'],search_one))
    return plan


def cache_items(items):
    now=time.monotonic()
    ttl=max(1,int(config['search'].get('cache_minutes',10)))*60
    for key,value in list(SEARCH_CACHE.items()):
        if now-value[0]>ttl:SEARCH_CACHE.pop(key,None)
    result=[]
    for item in items:
        token=uuid.uuid4().hex
        SEARCH_CACHE[token]=(now,item.copy())
        displayed=item.copy();displayed['id']=token
        displayed['has_torrent_file']=bool(item.get('download','').startswith(('http://','https://')))
        displayed.pop('download',None);displayed.pop('indexer_id',None)
        result.append(displayed)
    cap=max(50,int(config['search'].get('cache_max',350)))
    if len(SEARCH_CACHE)>cap:
        for key in list(SEARCH_CACHE)[:len(SEARCH_CACHE)-cap]:SEARCH_CACHE.pop(key,None)
    return result


def cache_stats():
    import sys
    return {'results':len(SEARCH_CACHE),'jobs':len(JOBS),
            'bytes_estimate':sum(sys.getsizeof(x)+sys.getsizeof(x[1]) for x in SEARCH_CACHE.values()),
            'ttl_minutes':config['search'].get('cache_minutes',10),'max_results':config['search'].get('cache_max',350),'disk_database_bytes':0}


@app.get('/api/cache')
async def get_cache():return cache_stats()


@app.delete('/api/cache')
async def clear_cache():
    removed=len(SEARCH_CACHE);SEARCH_CACHE.clear();JOBS.clear()
    return {'ok':True,'deleted':removed,'cache':cache_stats()}

def legacy_bytes():
    if not P_DATA.exists():return 0
    size=0
    for root,dirs,files in os.walk(P_DATA,followlinks=False):
        for name in files:
            try:size+=(pathlib.Path(root)/name).stat().st_size
            except OSError:pass
    return size


@app.get('/api/storage')
async def storage_info():
    return {'current_search_database_bytes':0,'legacy_prowlarr_bytes':legacy_bytes(),
            'legacy_path':str(P_DATA) if P_DATA.exists() else None}


@app.delete('/api/storage/legacy-prowlarr')
async def delete_legacy_storage():
    # Never touch Torrent Hub settings under /data/torrent-hub.
    if P_DATA.resolve()!=pathlib.Path('/data/prowlarr').resolve():
        raise HTTPException(400,'Небезопасный путь старых данных')
    before=legacy_bytes()
    if P_DATA.exists():
        shutil.rmtree(P_DATA)
    publish('Пользователь удалил старые файлы Prowlarr; настройки Torrent Hub сохранены')
    return {'ok':True,'bytes_deleted':before}


async def run_search_once(payload:Search):
    plan=await provider_plan(payload)
    if not plan:return {'items':[],'providers':[],'warning':'Не включено ни одного поискового источника'}
    async def one(key,name,fun):
        started=time.monotonic()
        try:
            items=await asyncio.wait_for(fun(),timeout=45)
            return key,name,items,None,round((time.monotonic()-started)*1000)
        except Exception as exc:
            return key,name,[],str(exc)[:230],round((time.monotonic()-started)*1000)
    values=await asyncio.gather(*(one(*x) for x in plan))
    providers=[{'id':key,'name':name,'ok':err is None,'count':len(items),'message':err or '', 'latency_ms':ms}
               for key,name,items,err,ms in values]
    return {'items':cache_items(merge_results([x[2] for x in values],config['search'].get('max_results',300))), 'providers':providers}


@app.post('/api/search')
async def search(payload:Search):
    return await run_search_once(payload)


async def job_worker(job_id,plan):
    job=JOBS[job_id]
    async def run(key,name,fn):
        started=time.monotonic()
        try:
            records=await asyncio.wait_for(fn(),timeout=45)
            job['groups'][key]=records
            job['providers'][key].update({'state':'ok' if records else 'empty','count':len(records),'latency_ms':round((time.monotonic()-started)*1000)})
        except Exception as exc:
            job['providers'][key].update({'state':'error','message':str(exc)[:220],'latency_ms':round((time.monotonic()-started)*1000)})
        job['updated']=time.monotonic()
    try:
        await asyncio.gather(*(run(*x) for x in plan))
    finally:
        job['done']=True
        job['updated']=time.monotonic()


@app.post('/api/search/start')
async def search_start(payload:Search):
    plan=await provider_plan(payload)
    now=time.monotonic()
    for jobid,j in list(JOBS.items()):
        if now-j['created']>900:JOBS.pop(jobid,None)
    if len(JOBS)>30:raise HTTPException(429,'Слишком много одновременных поисков')
    jid=uuid.uuid4().hex
    JOBS[jid]={'created':now,'updated':now,'done':not bool(plan),'groups':{},
               'providers':{key:{'id':key,'name':name,'state':'pending','count':0,'message':'','latency_ms':0} for key,name,_ in plan}}
    if plan:asyncio.create_task(job_worker(jid,plan))
    return {'job_id':jid}


@app.get('/api/search/job/{job_id}')
async def search_job(job_id:str):
    job=JOBS.get(job_id)
    if not job or time.monotonic()-job['created']>900:raise HTTPException(404,'Поиск не найден или устарел')
    # Stable search-result ids across polling rounds
    if 'cached' not in job or job['updated']!=job.get('cached_at'):
        job['cached']=cache_items(merge_results(list(job['groups'].values()),config['search'].get('max_results',300)))
        job['cached_at']=job['updated']
    return {'done':job['done'],'items':job['cached'],'providers':list(job['providers'].values())}


@app.post('/api/resolve')
async def resolve(payload:Resolve):
    r=SEARCH_CACHE.get(payload.id)
    if not r or time.monotonic()-r[0]>int(config['search'].get('cache_minutes',10))*60:raise HTTPException(404,'Поисковая раздача устарела: повторите поиск')
    item=r[1]
    if item['magnet']:return {'magnet':item['magnet']}
    link=item.get('download')
    if not link:raise HTTPException(400,'Трекер не предоставил ссылку для получения .torrent')
    try:
        if link.startswith('magnet:?'):magnet=link
        elif link.startswith(('http://','https://')):
            # Prevent requests to local/private arbitrary addresses, Prowlarr internal links use known local API only.
            u=urlsplit(link)
            if u.hostname in ('localhost','127.0.0.1') and u.port==9696 and u.path.startswith('/api/v1/indexer/'):
                resp=await prowlarr('GET',u.path+('?' + u.query if u.query else ''))
            else:
                resp=await fetch(config['proxy'],item.get('route','inherit'),link,timeout=25)
            magnet=magnet_from_torrent(resp.content)
        else:raise ValueError('Некорректная ссылка для скачивания')
        if not magnet_hash(magnet):raise ValueError('Получен некорректный magnet')
        item['magnet']=magnet;item['hash']=magnet_hash(magnet)
        return {'magnet':magnet}
    except Exception as exc:raise HTTPException(502,'Не удалось получить magnet: '+str(exc)[:220])



# Torrent bytes must be obtained from the origin, never fabricated from a magnet.
# A magnet/infohash alone does not contain the piece hashes and file layout of a .torrent.
async def torrent_file_for(token: str) -> tuple[bytes,str]:
    r=SEARCH_CACHE.get(token)
    if not r or time.monotonic()-r[0]>int(config['search'].get('cache_minutes',10))*60:
        raise HTTPException(404,'Раздача устарела. Повтори поиск.')
    item=r[1]
    link=str(item.get('download') or '')
    if not link.startswith(('http://','https://')):
        raise HTTPException(409,'Источник предоставил только magnet, исходного .torrent нет.')
    try:
        u=urlsplit(link)
        if u.hostname in ('localhost','127.0.0.1') and u.port==9696 and u.path.startswith('/api/v1/indexer/'):
            resp=await prowlarr('GET',u.path+('?' + u.query if u.query else ''))
        else:
            resp=await fetch(config['proxy'],item.get('route','inherit'),link,timeout=28)
        raw=resp.content
        if len(raw)>5_242_880:
            raise ValueError('Файл больше допустимых 5 МБ')
        extracted=magnet_from_torrent(raw) # validates the complete torrent, rejects HTML/errors
        if item.get('hash') and magnet_hash(extracted)!=item['hash']:
            raise ValueError('Торрент-файл не соответствует выбранной раздаче')
        name='torrenthub-'+hashlib.sha1(raw).hexdigest()[:18]+'.torrent'
        return raw,name
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502,'Не удалось получить .torrent: '+str(exc)[:210])


@app.get('/api/torrent/{token}')
async def download_torrent(token: str):
    raw,name=await torrent_file_for(token)
    return Response(raw,media_type='application/x-bittorrent',
                    headers={'Content-Disposition':f'attachment; filename="{name}"',
                             'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@app.post('/api/torrent/{token}/save-media')
async def save_torrent_to_media(token: str):
    raw,name=await torrent_file_for(token)
    target_dir=MEDIA_DIR
    try:
        target_dir.mkdir(parents=True,exist_ok=True)
        path=target_dir/name
        if path.exists():
            # Never overwrite a file already saved by the user.
            for i in range(1,1001):
                candidate=target_dir/(path.stem+f'-{i}.torrent')
                if not candidate.exists():
                    path=candidate;break
            else:raise ValueError('Слишком много файлов с одинаковым именем')
        with path.open('xb') as handle:
            handle.write(raw)
        publish('Сохранён torrent-файл в /media/TorrentHub')
        return {'ok':True,'path':str(path),'bytes':len(raw)}
    except (OSError,ValueError) as exc:
        raise HTTPException(507,'Не удалось сохранить в /media: '+str(exc)[:190])


@app.post('/api/send')
async def send(payload:Send):
    r=SEARCH_CACHE.get(payload.id)
    if not r:raise HTTPException(404,'Сначала выполните поиск')
    item=r[1]
    if not magnet_hash(item['magnet']):raise HTTPException(400,'Сначала получите magnet')
    if not payload.server_ids or len(payload.server_ids)>30:raise HTTPException(400,'Выберите TorrServer')
    results=[]
    for ident in dict.fromkeys(payload.server_ids):
        server=next((s for s in config['servers'] if s['id']==ident),None)
        if not server:results.append({'id':ident,'ok':False,'error':'Неизвестный сервер'});continue
        try:
            auth=(server['username'],server['password']) if server.get('username') else None
            async with httpx.AsyncClient(timeout=12,trust_env=False,auth=auth) as client:
                res=await client.post(sanitize_url(server['url'],private=True)+'/torrents',json={
                    'action':'add','link':item['magnet'],'title':item['title'],'save_to_db':True})
                res.raise_for_status()
            results.append({'id':ident,'ok':True})
        except Exception as exc:results.append({'id':ident,'ok':False,'error':str(exc)[:160]})
    publish('Отправка раздачи '+item['title'][:80]+': '+str(sum(x['ok'] for x in results))+'/' + str(len(results)))
    return {'results':results}


@app.get('/api/servers/{server_id}/library')
async def server_library(server_id:str):
    server=next((s for s in config['servers'] if s['id']==server_id),None)
    if not server:raise HTTPException(404,'TorrServer не найден')
    try:
        auth=(server.get('username',''),server.get('password','')) if server.get('username') else None
        async with httpx.AsyncClient(timeout=12,trust_env=False,auth=auth) as client:
            response=await client.post(sanitize_url(server['url'],private=True)+'/torrents',json={'action':'list'})
            response.raise_for_status()
        data=response.json()
        return {'items':data if isinstance(data,list) else []}
    except Exception as exc:raise HTTPException(502,'Ошибка библиотеки TorrServer: '+str(exc)[:170])


class ServerRemove(BaseModel):
    hash:str


@app.post('/api/servers/{server_id}/remove')
async def server_remove(server_id:str,payload:ServerRemove):
    if not re.fullmatch('[a-fA-F0-9]{40}',payload.hash):raise HTTPException(400,'Некорректный infohash')
    server=next((s for s in config['servers'] if s['id']==server_id),None)
    if not server:raise HTTPException(404,'TorrServer не найден')
    try:
        auth=(server.get('username',''),server.get('password','')) if server.get('username') else None
        async with httpx.AsyncClient(timeout=12,trust_env=False,auth=auth) as client:
            response=await client.post(sanitize_url(server['url'],private=True)+'/torrents',json={'action':'rem','hash':payload.hash})
            response.raise_for_status()
        publish('Удалена раздача с '+server['name'])
        return {'ok':True}
    except Exception as exc:raise HTTPException(502,'Ошибка удаления: '+str(exc)[:180])


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='0.0.0.0',port=8765,access_log=False,proxy_headers=False)
