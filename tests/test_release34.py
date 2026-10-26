"""Version 3.4.0: no-live-network regression tests for candidate aggregators."""
import asyncio
import json
import pathlib
import sys
import pytest

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'torrent-hub'/'app'))
import main
from external_sources import size_bytes,torapi_rows,torapi_detail_magnet


def test_torapi_normalizer_and_lazy_magnet():
    data={
      'RuTracker':[{'Name':'Отец / The Father [2020]','Id':'123456',
                    'Url':'https://rutracker.net/forum/viewtopic.php?t=123456',
                    'Torrent':'https://rutracker.net/forum/dl.php?t=123456',
                    'Size':'8.5 GB','Seeds':'42','Peers':'3'}],
      'Kinozal':[{'Name':'Отец (2020)','Id':'987','Size':'11.3 GB',
                  'Year':'2020','Language':'ПМ (LostFilm)','Format':'WEB-DL'}],
      'RuTor':{'Result':'No matches were found for your title'},
      'NoNameClub':[]}
    out=torapi_rows(data)
    assert len(out)==2
    assert out[0]['size']==int(8.5*1024**3)
    assert out[0]['torapi_provider']=='rutracker'
    assert 'download' not in out[0]
    assert out[1]['torapi_provider']=='kinozal'
    assert not torapi_detail_magnet({'Result':'Not found'})
    h='a'*40
    assert torapi_detail_magnet([{'Hash':h}]).startswith('magnet:?xt=urn%3Abtih%3A'+h)
    assert size_bytes('153.07 GB')>150*1024**3


def test_production_defaults_and_one_time_migration(monkeypatch,tmp_path):
    cfg=main.defaults()
    assert cfg['version']==6
    candidates={s['id']:s for s in cfg['sources']}
    assert {'torapi','torlock','jacred-su'}.issubset(candidates)
    assert all(not candidates[k]['enabled'] for k in ('torapi','torlock','jacred-su'))
    old=main.defaults()
    old['version']=5
    old['sources']=[s for s in old['sources'] if s['id'] not in ('torapi','torlock','jacred-su')]
    old['sources'][0]['route']='proxy'
    fn=tmp_path/'state.json'
    fn.write_text(json.dumps(old),encoding='utf8')
    monkeypatch.setattr(main,'STATE',fn)
    upgraded=main.load()
    assert len(upgraded['sources'])==len(old['sources'])+3
    assert upgraded['sources'][0]['route']=='proxy'
    assert main.load()['sources']==upgraded['sources']
    assert main.update_config(upgraded,{'sources':upgraded['sources']})['version']==6


def test_torapi_query_and_magnet_resolve(monkeypatch):
    events=[]
    infohash='b'*40
    async def fake(proxy,route,url,**kwargs):
        events.append((route,url,kwargs))
        class Response:
            content=b'{"RuTracker":[]}'
            def json(self):
                if '/search/title/all' in url:
                    return {'RuTracker':[{'Name':'Отец (2020) 1080p','Id':'123',
                                          'Size':'9.2 GB','Seeds':25}]}
                return [{'Hash':infohash}]
        return Response()
    monkeypatch.setattr(main,'fetch',fake)
    cfg=main.defaults()
    cfg['sources']=[s for s in cfg['sources'] if s['id']=='torapi']
    cfg['sources'][0]['enabled']=True
    monkeypatch.setattr(main,'config',cfg)
    result=asyncio.run(main.search_external(cfg['sources'][0],cfg['proxy'],'Отец'))
    assert len(result)==1
    assert result[0]['torapi_id']=='123'
    assert result[0]['torapi_provider']=='rutracker'
    assert not result[0]['download']
    assert events[0][1].endswith('/api/search/title/all')
    assert events[0][2]['params']['query']=='Отец'
    # Fresh cache token avoids external downloads.
    item=result[0]
    token='test-134'
    main.SEARCH_CACHE[token]=(main.time.monotonic(),item)
    response=asyncio.run(main.resolve(main.Resolve(id=token)))
    assert 'magnet' in response
    assert main.magnet_hash(response['magnet'])==infohash
    assert events[1][1].endswith('/api/search/id/rutracker')
    assert events[1][2]['params']=={'query':'123'}
    main.SEARCH_CACHE.pop(token,None)


def test_torapi_invalid_response_is_not_silent_success(monkeypatch):
    async def fake(*args,**kwargs):
        class Response:
            content=b'{bad}'
            def json(self):return {'error':'upstream unavailable'}
        return Response()
    monkeypatch.setattr(main,'fetch',fake)
    with pytest.raises(ValueError,match='TorAPI'):
        asyncio.run(main.search_external(main.defaults()['sources'][4],main.defaults()['proxy'],'Отец'))
