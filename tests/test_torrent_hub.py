import asyncio
import hashlib
import pathlib
import sys
from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'torrent-hub' / 'app'))
import core
import main

H='0123456789abcdef0123456789abcdef01234567'


def test_magnet_40_and_base32_roundtrip():
    b32='AERUKZ4JVPG66AJDIVTYTK6N54ASGRLH'
    assert core.magnet_hash(core.magnet_for(H,'Test')) == H
    assert core.btih(b32) == __import__('base64').b32decode(b32).hex()
    assert core.magnet_hash('magnet:?xt=urn:btih:BAD')==''


def test_merge_uses_infohash_and_prefers_seeders():
    a=core.result_of({'title':'Example','infoHash':H,'seeders':10},'A')
    b=core.result_of({'title':'Example','infoHash':H,'seeders':99},'B')
    assert len(core.merge_results([[a],[b]]))==1
    assert core.merge_results([[a],[b]])[0]['source']=='B'


def test_torrent_infohash_exact_bytes():
    info=b'd4:name4:demo6:lengthi12ee'
    tor=b'd8:announce18:udp://tracker.test4:info'+info+b'e'
    magnet=core.magnet_from_torrent(tor)
    assert core.magnet_hash(magnet)==hashlib.sha1(info).hexdigest()
    with pytest.raises(core.BencodeError):core.magnet_from_torrent(b'garbage')
    with pytest.raises(core.BencodeError):core.magnet_from_torrent(b'\x00'*6000000)


def test_private_torrserver_guard():
    assert core.sanitize_url('http://192.168.1.148:8090',private=True)
    with pytest.raises(ValueError):core.sanitize_url('https://example.com:8090',private=True)
    with pytest.raises(ValueError):core.sanitize_url('http://example.com@192.168.1.148:8090',private=True)


def test_network_routes_fail_closed():
    p={'mode':'proxy'}
    assert core.choose_routes(p,'inherit')==['proxy']
    assert core.choose_routes(p,'auto')==['direct','proxy']
    assert core.choose_routes(p,'direct')==['direct']
    with pytest.raises(ValueError):core.choose_routes(p,'nonsense')


def test_proxy_credentials_escape():
    url=core.proxy_address({'type':'socks5','host':'192.168.1.100','port':2080,'username':'name@1','password':'s:e/c'})
    assert 'name%401:s%3Ae%2Fc@192.168.1.100:2080' in url


def test_settings_draft_and_password_preserve():
    old=main.defaults()
    old['proxy']['password']='secret'
    old['servers'][0]['password']='other'
    after=main.update_config(old,{'proxy':{'host':'10.0.0.1','port':2080},'servers':[dict(old['servers'][0],password='')]})
    assert after['proxy']['host']=='10.0.0.1'
    assert after['proxy']['password']=='secret'
    assert after['servers'][0]['password']=='other'
    assert old['proxy']['host']=='192.168.1.100'


def test_settings_validation():
    old=main.defaults()
    with pytest.raises(ValueError):main.update_config(old,{'servers':[dict(old['servers'][0],url='https://google.com')]})
    with pytest.raises(ValueError):main.update_config(old,{'proxy':{'mode':'broken'}})
    with pytest.raises(ValueError):main.update_config(old,{'sources':[dict(old['sources'][0],kind='arbitrary')]})


def test_server_health_uses_get_echo(monkeypatch):
    calls=[]
    class Response:
        text='MatriX.147'
        def raise_for_status(self):pass
    class MockClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,url):calls.append(url);return Response()
    monkeypatch.setattr(main.httpx,'AsyncClient',MockClient)
    r=asyncio.run(main.check_server(main.defaults()['servers'][0]))
    assert r['ok']
    assert calls==['http://192.168.1.148:8090/echo']


def test_check_draft_never_changes_saved_config(monkeypatch):
    snapshot=main.config.copy()
    async def fake(p):return core.status(True,'OK')
    monkeypatch.setattr(main,'check_proxy',fake)
    old=main.config['proxy']['host']
    x=asyncio.run(main.run_check('proxy',draft={**main.config['proxy'],'host':'10.2.3.4'}))
    assert x['ok'] and main.config['proxy']['host']==old


def test_search_and_send_through_api(monkeypatch):
    h=core.magnet_for(H,'Ubuntu')
    async def mock_search(method,path,**kwargs):
        class R:
            def json(self):
                if path=='/api/v1/config/host':return {'proxyEnabled':True,'proxyType':'socks5','proxyHostname':'192.168.1.100','proxyPort':2080}
                return [{'title':'Ubuntu','infoHash':H,'seeders':30,'indexer':'Test'}]
        return R()
    monkeypatch.setattr(main,'prowlarr',mock_search)
    monkeypatch.setattr(main,'config',main.defaults())
    async def search_request():return await main.search(main.Search(query='ubuntu'))
    out=asyncio.run(search_request())
    assert len(out['items'])==1
    assert core.magnet_hash(out['items'][0]['magnet'])==H
    assert out['providers'][0]['ok']


def test_public_only_by_checkbox(monkeypatch):
    cfg=main.defaults();cfg['search']['enable_prowlarr']=False;[row.update(enabled=(i==0)) for i,row in enumerate(cfg['sources'])]
    monkeypatch.setattr(main,'config',cfg)
    async def fake_fetch(*args,**kwargs):
        class R:
            def json(self):return [{'title':'Ubuntu','magnet':core.magnet_for(H)}]
        return R()
    monkeypatch.setattr(main,'fetch',fake_fetch)
    a=asyncio.run(main.search(main.Search(query='ubuntu',include_public=False)))
    assert not a['items']
    b=asyncio.run(main.search(main.Search(query='ubuntu',include_public=True)))
    assert len(b['items'])==1


def test_jacred_direct_search_with_public_disabled(monkeypatch):
    cfg=main.defaults();cfg['search']['enable_prowlarr']=False;[row.update(enabled=(i==0)) for i,row in enumerate(cfg['sources'])];cfg['sources'][0]['group']='main'
    monkeypatch.setattr(main,'config',cfg)
    async def f(*args,**kwargs):
        class R:
            def json(self):return [{'title':'Demo','infoHash':H,'sid':21}]
        return R()
    monkeypatch.setattr(main,'fetch',f)
    r=asyncio.run(main.search(main.Search(query='demo',include_public=False)))
    assert len(r['items'])==1 and r['items'][0]['seeders']==21


def test_knaben_api_shape(monkeypatch):
    src={'id':'knaben','name':'Knaben','kind':'knaben','url':'https://api.knaben.org/v1','route':'proxy'}
    calls=[]
    async def f(*args,**kwargs):
        calls.append((args,kwargs))
        class R:
            def json(self):return {'hits':[{'title':'Ubuntu','hash':H.upper(),'bytes':100,'seeders':500,'peers':1,'details':'https://knaben.org/'}]}
        return R()
    monkeypatch.setattr(main,'fetch',f)
    arr=asyncio.run(main.search_external(src,main.defaults()['proxy'],'ubuntu'))
    assert arr[0]['seeders']==500 and arr[0]['hash']==H
    assert calls[0][1]['method']=='POST' and calls[0][1]['json']['query']=='ubuntu'


def test_no_external_http_redirect_to_local(monkeypatch):
    # Regression test on URL rejection at entry to the network layer.
    async def z():
        return await core.fetch(main.defaults()['proxy'],'direct','http://127.0.0.1/admin')
    with pytest.raises(ValueError):asyncio.run(z())


def test_server_send_select_only(monkeypatch):
    main.SEARCH_CACHE.clear()
    m=core.magnet_for(H,'Test')
    main.SEARCH_CACHE['test']=(__import__('time').monotonic(),{'magnet':m,'title':'Test'})
    monkeypatch.setattr(main,'config',main.defaults())
    seen=[]
    class Resp:
        def raise_for_status(self):pass
    class MockClient:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,json):seen.append((url,json));return Resp()
    monkeypatch.setattr(main.httpx,'AsyncClient',MockClient)
    x=asyncio.run(main.send(main.Send(id='test',server_ids=['lg'])))
    assert x['results']==[{'id':'lg','ok':True}]
    assert len(seen)==1 and seen[0][0]=='http://192.168.1.148:8090/torrents'
    assert seen[0][1]['save_to_db'] is True


def test_secrets_never_exposed_in_api(monkeypatch):
    cfg=main.defaults();cfg['proxy']['password']='SECRET_P';cfg['servers'][0]['password']='SECRET_T'
    monkeypatch.setattr(main,'config',cfg)
    snap=main.redacted()
    assert 'SECRET_P' not in repr(snap) and 'SECRET_T' not in repr(snap)
    assert snap['proxy']['password_set'] is True


def test_api_and_frontend_home():
    from fastapi.testclient import TestClient
    c=TestClient(main.app)
    res=c.get('/')
    assert res.status_code==200 and 'Torrent Hub' in res.text
    assert c.get('/api/config').status_code==200
    assert c.get('/healthz').json()['ok'] is True


def test_source_draft_http_no_config_change(monkeypatch):
    original=main.config
    called=[]
    async def fake(source,proxy):
        called.append(source.copy());return core.status(True,'ok')
    monkeypatch.setattr(main,'check_jacred',fake)
    draft={**main.defaults()['sources'][0],'url':'https://example.net','enabled':True}
    got=asyncio.run(main.run_check('source',draft['id'],draft))
    assert got['ok'] and called[0]['url']=='https://example.net'
    assert main.config is original


def test_prowlarr_block_when_proxy_not_synced(monkeypatch):
    cfg=main.defaults();monkeypatch.setattr(main,'config',cfg)
    async def unsynced(method,path,**kwargs):
        class R:
            def json(self):return {'proxyEnabled':False}
        return R()
    monkeypatch.setattr(main,'prowlarr',unsynced)
    with pytest.raises(RuntimeError, match='заблокирован'):
        asyncio.run(main.verify_engine_route())


def test_curated_definitions_install_without_network(tmp_path):
    import runner
    bundled=tmp_path/'bundled'; bundled.mkdir()
    (bundled/'rutor.yml').write_text('name: RuTor',encoding='utf8')
    (bundled/'noname-club.yml').write_text('name: NoNaMe Club',encoding='utf8')
    destination=tmp_path/'state'/'Definitions'/'Custom'
    assert runner.install_bundled_definitions(bundled,destination)==['rutor.yml','noname-club.yml']
    assert (destination/'rutor.yml').read_text()=='name: RuTor'
    (destination/'rutor.yml').write_text('name: Custom RuTor')
    assert runner.install_bundled_definitions(bundled,destination)==[]
    assert (destination/'rutor.yml').read_text()=='name: Custom RuTor'

def test_curated_ui_is_not_big_catalog():
    ui=(pathlib.Path(__file__).parents[1]/'torrent-hub/app/static/index.html').read_text()
    assert 'Основные торрент-трекеры' in ui
    assert 'NNM-Club' in ui and 'RuTor' in ui and 'RuTracker' in ui
    assert 'Дополнительно: найти другой трекер' in ui
