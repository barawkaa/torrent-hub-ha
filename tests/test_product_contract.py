"""Release acceptance tests for the previously broken user journeys."""
import asyncio
import pathlib
import sys
from unittest.mock import Mock
import pytest
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'torrent-hub'/'app'))
import core
import main

HASH='a'*40

def run(x):return asyncio.run(x)

def test_first_run_includes_all_public_sources():
    c=main.defaults()
    assert c['version']==4
    assert not c['search']['enable_prowlarr']
    assert len(c['direct_trackers'])==5
    assert sum(t['enabled'] for t in c['direct_trackers'])==3
    assert c['search']['include_public']
    assert all(x['enabled'] for x in c['sources'])
    assert all(x['route']=='inherit' for x in c['sources'])


def test_jacred_real_response_and_magnet(monkeypatch):
    async def fake(*args,**kwargs):
        class Response:
            def json(self):return [{'title':'Интерстеллар 1080p','magnet':core.magnet_for(HASH),'size':1456789,'sid':123,'createTime':'2026-01-10'}]
        return Response()
    monkeypatch.setattr(main,'fetch',fake)
    cfg=main.defaults()
    found=run(main.search_external(cfg['sources'][0],cfg['proxy'],'Интерстеллар'))
    assert len(found)==1
    assert found[0]['seeders']==123
    assert found[0]['date']=='2026-01-10'
    assert found[0]['source_id']=='jr-maxvol'


def test_torznab_feed_and_auth(monkeypatch):
    sent=[]
    async def fake(*args,**kwargs):
        sent.append((args,kwargs))
        class Response:
            content=f'''<rss xmlns:torznab="http://torznab.com/schemas/2015/feed"><channel><item><title>Movie HDR 2160p</title><link>magnet:?xt=urn:btih:{HASH}</link><torznab:attr name="seeders" value="35"/><torznab:attr name="infohash" value="{HASH}"/><enclosure url="magnet:?xt=urn:btih:{HASH}" length="987654321"/></item></channel></rss>'''.encode()
        return Response()
    monkeypatch.setattr(main,'fetch',fake)
    src={'id':'mine','name':'My Torznab','kind':'torznab','url':'https://indexer.example/api','route':'proxy','api_key':'secret'}
    arr=run(main.search_external(src,main.defaults()['proxy'],'movie'))
    assert arr[0]['magnet'].startswith('magnet:?') and arr[0]['seeders']==35
    assert sent[0][1]['params']['apikey']=='secret'


def test_api_empty_result_reports_empty_not_alive_results(monkeypatch):
    async def fake(*args,**kwargs):return []
    monkeypatch.setattr(main,'search_external',fake)
    res=run(main.check_jacred(main.defaults()['sources'][0],main.defaults()['proxy']))
    assert res['ok'] and res['count']==0 and res['state']=='empty'


def test_provider_selection_skips_unselected(monkeypatch):
    monkeypatch.setattr(main,'config',main.defaults())
    plan=run(main.provider_plan(main.Search(query='matrix',selected_sources=['jac-black'])))
    assert [x[0] for x in plan]==['jac-black']
    plan2=run(main.provider_plan(main.Search(query='matrix')))
    assert len(plan2)==len(main.config['sources'])+sum(t['enabled'] for t in main.config['direct_trackers'])


def test_partial_source_errors_do_not_hide_other_results(monkeypatch):
    monkeypatch.setattr(main,'config',main.defaults())
    async def fake(s,p,q):
        if s['id']=='jac-black': raise TimeoutError('timeout to public search API')
        return [dict(core.result_of({'title':'Example','infoHash':HASH,'seeders':30},s['name']),route='proxy',source_id=s['id'])]
    monkeypatch.setattr(main,'search_external',fake)
    cfg=main.config
    cfg['search']['enable_prowlarr']=False
    response=run(main.run_search_once(main.Search(query='example',selected_sources=[s['id'] for s in cfg['sources']])))
    assert len(response['items'])==1
    assert any(not p['ok'] and p['id']=='jac-black' for p in response['providers'])
    assert any(p['ok'] and p['count']==1 for p in response['providers'])


def test_search_progress_job_contains_source_counts(monkeypatch):
    monkeypatch.setattr(main,'config',main.defaults())
    main.config['search']['enable_prowlarr']=False
    async def fake(src,proxy,q):
        await asyncio.sleep(.01)
        return [dict(core.result_of({'title':'Film','infoHash':HASH,'seeders':15},src['name']),source_id=src['id'])]
    monkeypatch.setattr(main,'search_external',fake)
    async def scenario():
        created=await main.search_start(main.Search(query='film',selected_sources=[s['id'] for s in main.config['sources']]))
        jid=created['job_id']
        assert len((await main.search_job(jid))['providers'])==4
        await asyncio.sleep(.08)
        finish=await main.search_job(jid)
        assert finish['done'] and len(finish['items'])==1
        assert all(x['state']=='ok' and x['count']==1 for x in finish['providers'])
    run(scenario())


def test_password_hidden_and_preserved():
    original=main.defaults()
    original['sources'][0]['api_key']='sensitive'
    updated=main.update_config(original,{'sources':[dict(original['sources'][0],api_key='')]})
    assert updated['sources'][0]['api_key']=='sensitive'
    assert original['sources'][0]['api_key']=='sensitive'


def test_preconfigured_local_torrservers():
    servers=main.defaults()['servers']
    assert [x['url'] for x in servers]==['http://192.168.1.148:8090','http://192.168.1.100:8090']


def test_default_indexer_autoinstall(monkeypatch):
    calls=[]
    responses={'/api/v1/indexer':[], '/api/v1/indexer/schema':[
        {'name':'RuTor','definitionName':'rutor','protocol':'torrent'},
        {'name':'NoNaMe Club','definitionName':'noname-club','protocol':'torrent'},
        {'name':'MegaPeer','definitionName':'megapeer','protocol':'torrent'}]}
    async def fake(method,path,**kwargs):
        calls.append((method,path,kwargs))
        obj=responses.get(path,{'name':kwargs.get('json',{}).get('name')})
        return Mock(json=lambda:obj)
    monkeypatch.setattr(main,'prowlarr',fake)
    monkeypatch.setattr(main,'AUTO_INSTALL_DONE',False)
    run(main.install_default_indexers())
    posted=[x for x in calls if x[0]=='POST']
    assert len(posted)==3
    assert main.AUTO_INSTALL_DONE


def test_external_url_rejects_internal_address():
    with pytest.raises(ValueError):core.external_url_guard('http://127.0.0.1:8123')
    with pytest.raises(ValueError):core.external_url_guard('http://192.168.1.100:8090')


def test_torrent_icon_route_exists():
    from fastapi.testclient import TestClient
    response=TestClient(main.app).get('/icon.png')
    assert response.status_code==200
    assert response.headers['content-type'].startswith('image/png')
    assert response.content.startswith(b'\\x89PNG'.decode('unicode_escape').encode('latin1'))


def test_saved_proxy_validation_uses_preserved_password(monkeypatch):
    cfg=main.defaults()
    cfg['proxy']['password']='secret-password'
    monkeypatch.setattr(main,'config',cfg)
    seen=[]
    async def fake(settings):
        seen.append(settings.copy())
        return core.status(True,'OK')
    monkeypatch.setattr(main,'check_proxy',fake)
    assert run(main.run_check('proxy',draft={'host':'192.168.1.100','password':''}))['ok']
    assert seen[0]['password']=='secret-password'
    assert cfg['proxy']['password']=='secret-password'


def test_download_real_torrent_and_refuse_fake_metadata(monkeypatch):
    import time
    info=b'd4:name4:demo6:lengthi12ee'
    torrent=b'd8:announce18:udp://tracker.test4:info'+info+b'e'
    sha=__import__('hashlib').sha1(info).hexdigest()
    main.SEARCH_CACHE['real-file']=(time.monotonic(),{'download':'https://example.org/file.torrent','hash':sha,'route':'proxy'})
    class FakeResponse:
        content=torrent
    async def fake(*args,**kwargs):return FakeResponse()
    monkeypatch.setattr(main,'fetch',fake)
    blob,name=run(main.torrent_file_for('real-file'))
    assert blob==torrent and name.endswith('.torrent')
    main.SEARCH_CACHE['real-file']=(time.monotonic(),{'download':'https://example.org/file.torrent','hash':'f'*40,'route':'proxy'})
    with pytest.raises(main.HTTPException) as exc:run(main.torrent_file_for('real-file'))
    assert exc.value.status_code==502
    main.SEARCH_CACHE['magnet-only']=(time.monotonic(),{'download':'','hash':sha})
    with pytest.raises(main.HTTPException) as exc2:run(main.torrent_file_for('magnet-only'))
    assert exc2.value.status_code==409


def test_save_torrent_to_media_does_not_overwrite(monkeypatch,tmp_path):
    monkeypatch.setattr(main,'MEDIA_DIR',tmp_path)
    async def fake(_token):return b'dummy','safe.torrent'
    monkeypatch.setattr(main,'torrent_file_for',fake)
    first=run(main.save_torrent_to_media('one'))
    second=run(main.save_torrent_to_media('one'))
    assert first['ok'] and second['ok']
    assert first['path'] != second['path']
    assert (tmp_path/'safe.torrent').read_bytes()==b'dummy'


def test_offline_definitions_migrate_without_duplicates(tmp_path):
    import runner,hashlib,json
    source=tmp_path/'bundled'
    source.mkdir()
    destination=tmp_path/'Definitions'
    legacy=destination/'Custom'
    legacy.mkdir(parents=True)
    data=b'id: rutor\\nname: RuTor\\n'
    (source/'rutor.yml').write_bytes(data)
    (legacy/'rutor.yml').write_bytes(data)
    digest=hashlib.sha256(data).hexdigest()
    (legacy/'.torrenthub-managed.json').write_text(json.dumps({'rutor.yml':digest}),encoding='utf8')
    result=runner.install_bundled_definitions(source,destination)
    assert 'rutor.yml' in result
    assert (destination/'rutor.yml').read_bytes()==data
    assert not (legacy/'rutor.yml').exists()
    assert runner.install_bundled_definitions(source,destination)==[]
    (destination/'rutor.yml').write_text('user-edited',encoding='utf8')
    runner.install_bundled_definitions(source,destination)
    assert (destination/'rutor.yml').read_text(encoding='utf8')=='user-edited'


def test_indexer_provisioning_retries_failed_install(monkeypatch):
    import main
    responses={'/api/v1/indexer':[], '/api/v1/indexer/schema':[
        {'name':'RuTor','definitionName':'rutor','protocol':'torrent'},
        {'name':'NoNaMe Club','definitionName':'noname-club','protocol':'torrent'},
        {'name':'MegaPeer','definitionName':'megapeer','protocol':'torrent'}]}
    async def fake(method,path,**kwargs):
        from unittest.mock import Mock
        if method=='POST':raise RuntimeError('external tracker temporarily unavailable')
        return Mock(json=lambda:responses.get(path,[]))
    monkeypatch.setattr(main,'prowlarr',fake)
    monkeypatch.setattr(main,'AUTO_INSTALL_DONE',False)
    run(main.install_default_indexers())
    assert not main.AUTO_INSTALL_DONE
