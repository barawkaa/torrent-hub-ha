"""Torrent Hub backend — HA ingress, bundled indexer engine, checks and TorrServer control."""
from __future__ import annotations
import asyncio
import copy
import json
import logging
import os
import pathlib
import secrets
import time
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from contextlib import asynccontextmanager
from urllib.parse import quote, urlsplit

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import (BencodeError, btih, choose_routes, fetch, magnet_for, magnet_from_torrent,
                  magnet_hash, merge_results, proxy_address, result_of, sanitize_url, status)

BASE = pathlib.Path(__file__).resolve().parent
DATA = pathlib.Path(os.environ.get('TORRENT_HUB_DATA','/data/torrent-hub'))
P_DATA = pathlib.Path(os.environ.get('PROWLARR_DATA','/data/prowlarr'))
STATE = DATA/'state.json'
P_URL='http://127.0.0.1:9696'
log=logging.getLogger('torrent_hub')
logging.basicConfig(level=logging.INFO)
MUTEX=asyncio.Lock()
CHECKS={}
EVENTS=deque(maxlen=200)
SEARCH_CACHE={}


def defaults():
    return {'proxy': {'mode':'proxy','type':'socks5','host':'192.168.1.100','port':2080,'username':'','password':''},
            'sources':[
                {'id':'jr-maxvol','name':'jr.maxvol.pro','url':'https://jr.maxvol.pro','enabled':False,'group':'public','route':'inherit','kind':'jacred'},
                {'id':'jac-black','name':'jac.black','url':'https://jac.black','enabled':False,'group':'public','route':'inherit','kind':'jacred'},
                {'id':'ru-jac-black','name':'ru.jac.black','url':'https://ru.jac.black','enabled':False,'group':'public','route':'inherit','kind':'jacred'},
                {'id':'knaben','name':'Knaben API','url':'https://api.knaben.org/v1','enabled':False,'group':'public','route':'inherit','kind':'knaben'}],
            'servers':[
                {'id':'lg','name':'LG — приставка','url':'http://192.168.1.148:8090','username':'','password':''},
                {'id':'ha','name':'Home Assistant','url':'http://192.168.1.100:8090','username':'','password':''}],
            'search':{'enable_prowlarr':True,'check_interval':300,'max_results':300,'remember_history':False}}


def load():
    if STATE.exists():
        value=json.loads(STATE.read_text('utf8'))
        if isinstance(value,dict) and 'proxy' in value and 'servers' in value:
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
    url=sanitize_url(src['url'])
    if src.get('kind','jacred')=='jacred':
        r=await fetch(proxy,src.get('route','inherit'),url+'/api/v1.0/torrents',params={'search':query},timeout=25)
        obj=r.json()
        if isinstance(obj,dict):obj=obj.get('results',obj.get('torrents',obj.get('data')))
    elif src.get('kind')=='knaben':
        r=await fetch(proxy,src.get('route','inherit'),url,method='POST',json={'query':query,'size':100,'hide_unsafe':True},timeout=25)
        response=r.json()
        obj=response.get('hits') if isinstance(response,dict) else None
        if isinstance(obj,list):
            obj=[{'title':row.get('title'), 'infoHash':row.get('hash'),
                  'magnetUrl':row.get('magnetUrl'), 'size':row.get('bytes'),
                  'seeders':row.get('seeders'), 'leechers':row.get('peers'),
                  'url':row.get('details'), 'tracker':row.get('cachedOrigin')}
                 for row in obj if isinstance(row,dict)]
    else:raise ValueError('Неизвестный тип API')
    if not isinstance(obj,list):raise ValueError('API не вернул массив поисковых результатов')
    return [result_of(row,src['name']) for row in obj if isinstance(row,dict)]


async def check_jacred(source:dict,proxy:dict,probe=True) -> dict:
    start=time.monotonic()
    try:
        obj=await search_external(source,proxy,'ubuntu' if probe else 'test')
        return status(True,f'Поисковое API ответило, тестовых результатов: {len(obj)}',round((time.monotonic()-start)*1000),count=len(obj))
    except Exception as exc:
        return status(False,'Ошибка поискового API: '+str(exc))


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
        value=await check_proxy(draft or proxy)
    elif category=='prowlarr':
        value=await check_prowlarr()
    elif category=='server':
        value=await check_server(draft or next(x for x in config['servers'] if x['id']==entry))
    elif category=='source':
        value=await check_jacred(draft or next(x for x in config['sources'] if x['id']==entry),proxy)
    elif category=='all':
        async def task(category,name,item=None):
            try:
                r=await run_check(category,name,item)
                CHECKS[category+(':'+name if name else '')]=r
            except Exception as exc:
                CHECKS[category+(':'+name if name else '')]=status(False,str(exc))
        await asyncio.gather(task('proxy',None),task('prowlarr',None),
            *(task('server',s['id']) for s in config['servers']),
            *(task('source',s['id']) for s in config['sources'] if s.get('enabled')))
        return CHECKS
    else: raise ValueError('Некорректная категория')
    if draft is None: CHECKS[category+(':'+entry if entry else '')]=value
    return value


async def monitoring():
    while True:
        try:
            await run_check('all')
        except Exception as exc:
            log.error('check cycle: %s',exc)
        await asyncio.sleep(max(60,int(config['search'].get('check_interval',300))))


@asynccontextmanager
async def lifespan(_app):
    monitor=asyncio.create_task(monitoring())
    yield
    monitor.cancel()
    try:await monitor
    except asyncio.CancelledError:pass

app=FastAPI(title='Torrent Hub',docs_url=None,redoc_url=None,lifespan=lifespan)


class Search(BaseModel):
    query:str=Field(min_length=2,max_length=160)
    include_public:bool=False

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

@app.get('/healthz')
async def health(): return {'ok':True,'component':'torrent-hub'}

@app.get('/api/status')
async def overall(): return {'checks':CHECKS,'events':list(EVENTS)[:50], 'engine_key_ready':bool(prowlarr_key())}

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
                    'kind':item.get('kind','jacred')})
                if row['group'] not in ('main','public') or row['route'] not in ('inherit','direct','proxy','auto') or row['kind'] not in ('jacred','knaben'):
                    raise ValueError('Неверный тип поискового источника')
            else:
                row['username']=str(item.get('username',''))[:120]
                row['password']=str(item.get('password') or previous.get(uid,{}).get('password',''))[:200]
                if item.get('clear_password'):row['password']=''
            clean.append(row)
        out[collection]=clean
    if 'search' in body:
        s=body['search'];out['search']['enable_prowlarr']=bool(s.get('enable_prowlarr',out['search']['enable_prowlarr']))
        if 'check_interval' in s:
            seconds=int(s['check_interval'])
            if seconds<60 or seconds>3600:raise ValueError('Интервал проверок 60–3600 секунд')
            out['search']['check_interval']=seconds
    return out


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


@app.post('/api/engine-proxy')
async def configure_engine_proxy():
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


@app.post('/api/search')
async def search(payload:Search):
    query=payload.query.strip()
    if len(query)<2:raise HTTPException(400,'Введите название')
    tasks=[];names=[]
    if config['search']['enable_prowlarr']:
        async def ps():
            await verify_engine_route()
            rows=(await prowlarr('GET','/api/v1/search',params={'query':query})).json()
            return [result_of(row,'Prowlarr') for row in (rows if isinstance(rows,list) else [])]
        names.append('Prowlarr');tasks.append(ps())
    for s in config['sources']:
        if not s['enabled'] or s['group']=='public' and not payload.include_public:continue
        async def js(src):
            return await search_external(src,config['proxy'],query)
        names.append(s['name']);tasks.append(js(s))
    if not tasks:return {'items':[],'providers':[],'warning':'Не подключено ни одного поискового источника'}
    responses=await asyncio.gather(*tasks,return_exceptions=True)
    groups=[];providers=[]
    for name,response in zip(names,responses):
        if isinstance(response,Exception):providers.append({'name':name,'ok':False,'message':str(response)[:160]})
        else:groups.append(response);providers.append({'name':name,'ok':True,'count':len(response)})
    items=merge_results(groups,config['search'].get('max_results',300))
    # A small server-side TTL cache makes torrent resolving safe without fetching arbitrary client-provided URLs.
    now=time.monotonic()
    for key,value in list(SEARCH_CACHE.items()):
        if now-value[0]>900:SEARCH_CACHE.pop(key,None)
    for item in items:
        token=uuid.uuid4().hex
        SEARCH_CACHE[token]=(now,item.copy())
        item['id']=token
        item.pop('download',None);item.pop('indexer_id',None)
    return {'items':items,'providers':providers}


@app.post('/api/resolve')
async def resolve(payload:Resolve):
    r=SEARCH_CACHE.get(payload.id)
    if not r or time.monotonic()-r[0]>900:raise HTTPException(404,'Поисковая раздача устарела: повторите поиск')
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
                resp=await fetch(config['proxy'],'inherit',link,timeout=25)
            magnet=magnet_from_torrent(resp.content)
        else:raise ValueError('Некорректная ссылка для скачивания')
        if not magnet_hash(magnet):raise ValueError('Получен некорректный magnet')
        item['magnet']=magnet;item['hash']=magnet_hash(magnet)
        return {'magnet':magnet}
    except Exception as exc:raise HTTPException(502,'Не удалось получить magnet: '+str(exc)[:220])


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


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='0.0.0.0',port=8765,access_log=False,proxy_headers=False)
