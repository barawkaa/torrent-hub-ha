"""Source-specific normalization for documented public torrent search APIs.

This module is intentionally pure: no outbound network requests or tracker credentials.
TorAPI /api/search/title/all and /api/search/id/<provider>:
https://github.com/Lifailon/TorAPI/blob/main/docs.md
"""
from __future__ import annotations

import re

from core import btih, magnet_for, magnet_hash

TORAPI_PROVIDERS = frozenset(('rutracker','kinozal','rutor','nonameclub'))
_GIB = 1024**3


def size_bytes(value) -> int:
    """Convert TorAPI human units to bytes; never confuse GB with byte counts."""
    if isinstance(value,(float,int)):
        return max(0,int(value))
    s=str(value or '').strip().replace(',','.')
    match=re.match(r'^([\d.]+)\s*(B|KB|KiB|MB|MiB|GB|GiB|TB|TiB|байт|КБ|МБ|ГБ|ТБ)?$',s,re.I)
    if not match:return 0
    try:amount=float(match.group(1))
    except (ValueError,OverflowError):return 0
    if not 0<=amount<=1e9:return 0
    units={'':1,'B':1,'байт':1,'KB':1024,'KIB':1024,'КБ':1024,
           'MB':1024**2,'MIB':1024**2,'МБ':1024**2,
           'GB':_GIB,'GIB':_GIB,'ГБ':_GIB,
           'TB':1024**4,'TIB':1024**4,'ТБ':1024**4}
    return int(amount*units.get((match.group(2) or '').upper(),1))


def torapi_rows(payload) -> list[dict]:
    """Flatten four provider result arrays; ignore 'no results' objects."""
    if not isinstance(payload,dict):
        raise ValueError('TorAPI: ожидался объект с результатами по трекерам')
    result=[]
    for key,rows in payload.items():
        provider=str(key).lower()
        if provider not in TORAPI_PROVIDERS or not isinstance(rows,list):
            continue
        for row in rows[:100]:
            if not isinstance(row,dict):continue
            title=str(row.get('Name') or row.get('Title') or '').strip()
            if not title:continue
            item_id=str(row.get('Id') or '')
            raw_hash=str(row.get('Hash') or '')
            # Public title search may omit infohash. Never invent one.
            clean_hash=btih(raw_hash)
            info_url=str(row.get('Url') or '')
            # Direct .torrent downloads may require login: do not offer them.
            result.append({
                'title':title[:400],
                'url':info_url[:1400] if info_url.startswith(('https://','http://')) else '',
                'size':size_bytes(row.get('Size')),
                'seeders':row.get('Seeds',0),
                'leechers':row.get('Peers',0),
                'date':str(row.get('Date') or '')[:40],
                'hash':clean_hash,
                'magnet':str(row.get('Magnet') or ''),
                'tracker':'TorAPI · '+key,
                'name':str(row.get('Title') or '')[:160],
                'originalname':str(row.get('Original_Name') or '')[:160],
                'year':row.get('Year'),
                'voices':str(row.get('Language') or '')[:120],
                'torapi_provider':provider,
                'torapi_id':item_id if re.fullmatch(r'\d{1,12}',item_id) else ''
            })
    if not any(isinstance(payload.get(k),list) for k in payload if k.lower() in TORAPI_PROVIDERS):
        if 'error' in payload or 'Error' in payload:
            raise ValueError('TorAPI сообщил об ошибке: '+str(payload.get('error') or payload.get('Error'))[:120])
    return result


def torapi_detail_magnet(payload, title='') -> str:
    """Require a real 40-hex (or base32) infohash from the ID endpoint."""
    if isinstance(payload,list):rows=payload
    elif isinstance(payload,dict):rows=[payload]
    else:rows=[]
    for row in rows[:4]:
        if not isinstance(row,dict):continue
        magnet=str(row.get('Magnet') or row.get('magnet') or '')
        if magnet_hash(magnet):return magnet
        hashcode=btih(str(row.get('Hash') or row.get('hash') or ''))
        if hashcode:return magnet_for(hashcode,title)
    return ''
