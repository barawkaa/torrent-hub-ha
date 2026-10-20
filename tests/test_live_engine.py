import pathlib
import sys
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]/'torrent-hub'/'app'))
import main
import direct

H="0123456789abcdef0123456789abcdef01234567"
RUTOR_HTML=f"""<html><body><table><tr><td>20 Окт 26</td><td><a href="/torrent/111">Титаник 1997 1080p</a>
<a href="magnet:?xt=urn:btih:{H}">M</a><a class="downgif" href="/download/111">D</a></td>
<td>6.5 GB</td><td><span class="green">165</span></td></tr></table></body></html>"""
MEGAPEER_HTML="""<html><table><tr class="table_fon"><td>20 Окт 26</td><td>
<a href="/torrent/222">Кино 1080p</a><a href="/download/222">Скачать</a></td>
<td>2.0 GB</td><td><font>45</font><font>0</font></td></tr></table></html>"""
NNM_HTML="""<html><table class="forumline tablesorter"><tbody><tr>
<td></td><td><a href="viewtopic.php?t=55"><b>Фильм 4K</b></a><a href="download.php?id=55">download</a></td>
<td></td><td></td><td></td><td><u>2.6 GB</u></td><td class="seedmed"><b>29</b></td><td></td><td><u>1770000000</u></td>
</tr></tbody></table></html>"""

def test_parse_real_site_layouts():
    a=direct.parse_rutor(RUTOR_HTML,"https://rutor.info")
    assert len(a)==1 and a[0]["seeders"]==165 and a[0]["size"]>6_000_000_000
    assert a[0]["hash"]==H and a[0]["date"]=="2026-10-20"
    b=direct.parse_megapeer(MEGAPEER_HTML,"https://megapeer.vip")
    assert len(b)==1 and b[0]["seeders"]==45 and b[0]["download"].endswith("/download/222")
    c=direct.parse_nnmclub(NNM_HTML,"https://nnmclub.to")
    assert len(c)==1 and c[0]["seeders"]==29

def test_reject_bot_challenge():
    import pytest
    with pytest.raises(ValueError):direct.parse_rutor("<html>Just a moment...</html>","https://rutor.info")

def test_default_has_no_file_database_or_prowlarr():
    cfg=main.defaults()
    assert not cfg["search"]["enable_prowlarr"]
    assert all(x["enabled"] for x in cfg["direct_trackers"])
    assert set(x["kind"] for x in cfg["direct_trackers"])=={"rutor","nnmclub","megapeer"}
    assert main.cache_stats()["disk_database_bytes"]==0

def test_cache_clears_without_disk(monkeypatch):
    cfg=main.defaults()
    monkeypatch.setattr(main,'config',cfg)
    main.SEARCH_CACHE.clear()
    main.cache_items([dict(main.result_of({"title":"Titanic","infoHash":H},"RuTor"),source_id="rutor")])
    assert main.cache_stats()["results"]==1
    client=TestClient(main.app)
    response=client.delete("/api/cache")
    assert response.status_code==200 and response.json()["cache"]["results"]==0

def test_ui_contract_and_proxy_and_server_config():
    c=TestClient(main.app)
    assert c.get("/").status_code==200
    assert c.get("/api/direct-trackers").status_code==200
    assert c.get("/icon.png").status_code==200
    conf=c.get("/api/config").json()
    assert conf["proxy"]["password_set"] in (True,False)
    assert len(conf["servers"])>=2
    html=c.get("/").text
    assert 'Очистить кеш' in html and 'Прямой поиск по трекерам' in html

def test_provider_plan_disables_aggregators_without_disabling_live(monkeypatch):
    import asyncio
    cfg=main.defaults()
    monkeypatch.setattr(main,"config",cfg)
    plan=asyncio.run(main.provider_plan(main.Search(query="Титаник",include_public=False)))
    assert len(plan)==3
    assert all(x[0].startswith("direct:") for x in plan)
