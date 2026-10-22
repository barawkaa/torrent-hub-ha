"""Acceptance tests for Torrent Hub 3.2.0 without live network dependencies."""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'torrent-hub'/'app'))
import core
import main


def test_smart_defaults_and_source_contract():
    cfg=main.defaults()
    assert (cfg['smart']['min_size'], cfg['smart']['max_size']) == (7,150)
    assert any(s['id']=='knaben' and s['kind']=='knaben_v2' for s in cfg['sources'])


def test_preserve_custom_smart_preferences(monkeypatch,tmp_path):
    state=tmp_path/'state.json'
    cfg=main.defaults()
    cfg['smart'].update(min_size=5,max_size=10)
    cfg['sources'][-1].update(kind='knaben',url='https://api.knaben.org/v1')
    state.write_text(json.dumps(cfg),encoding='utf8')
    monkeypatch.setattr(main,'STATE',state)
    upgraded=main.load()
    assert upgraded['smart']['min_size']==7
    assert upgraded['smart']['max_size']==150
    assert upgraded['sources'][-1]['kind']=='knaben_v2'
    cfg['smart'].update(min_size=12,max_size=20)
    state.write_text(json.dumps(cfg),encoding='utf8')
    custom=main.load()
    assert custom['smart']['min_size']==12
    assert custom['smart']['max_size']==20


def test_preserve_observed_video_metadata_during_merge():
    h='a'*40
    a=core.result_of(dict(title='Отец / The Father (2020)',infoHash=h,
        relased=2020,voices='LostFilm',quality=1080,name='Отец',
        originalname='The Father',types=['фильм']), 'JacRed')
    b=core.result_of(dict(title='Отец / The Father (2020)',infoHash=h,
        seeders=99),'Direct')
    result=core.merge_results([[a],[b]])[0]
    assert result['seeders']==99
    assert result['name']=='Отец'
    assert result['release_year']==2020
    assert result['video_quality']=='1080'
    assert result['voices']=='LostFilm'
    assert result['content_types']==['фильм']


def test_knaben_v2_request_and_shape(monkeypatch):
    observed=[]
    async def fake(proxy,route,url,**kwargs):
        observed.append((url,kwargs))
        class Response:
            def json(self):
                return {'hits':[{'title':'The Father (2020) 1080p',
                        'hash':'b'*40,'bytes':1234567,'seeders':25,
                        'name':'Отец','year':2020}]}
        return Response()
    monkeypatch.setattr(main,'fetch',fake)
    src=dict(id='knaben',name='Knaben',kind='knaben_v2',
             url='https://api.knaben.org',route='inherit')
    out=asyncio.run(main.search_external(src,main.defaults()['proxy'],'Отец'))
    assert observed[0][0]=='https://api.knaben.org/v2/search'
    assert observed[0][1]['params']['q']=='Отец'
    assert len(out)==1 and out[0]['seeders']==25
    assert out[0]['release_year']==2020


def test_public_checkbox_disables_every_aggregator_even_old_main_group(monkeypatch):
    cfg=main.defaults()
    cfg['sources'][0]['group']='main'
    monkeypatch.setattr(main,'config',cfg)
    plan=asyncio.run(main.provider_plan(main.Search(query='Отец',include_public=False)))
    assert len(plan)==sum(x['enabled'] for x in cfg['direct_trackers'])
    assert all(x[0].startswith('direct:') for x in plan)
