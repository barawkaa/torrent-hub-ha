"""Release tests for the local web interface, HA Ingress, PWA, and search."""
import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"torrent-hub"/"app"))
import main
import gateway
import serve
import direct


@pytest.fixture
def isolated(monkeypatch,tmp_path):
    config=main.defaults()
    monkeypatch.setattr(main,"config",config)
    monkeypatch.setattr(main,"DATA",tmp_path)
    monkeypatch.setattr(main,"STATE",tmp_path/"state.json")
    monkeypatch.setattr(gateway.backend,"DATA",tmp_path)
    monkeypatch.setattr(main,"CHECKS",{})
    yield config


def test_local_web_opens_immediately_without_login(isolated):
    with TestClient(gateway.external_app) as c:
        assert c.get("/",follow_redirects=False).status_code==200
        assert c.get("/api/config").status_code==200
        assert c.get("/api/direct-trackers").status_code==200
        assert c.get("/manifest.webmanifest").status_code==200
        assert c.get("/icon.png").status_code==200
        assert c.get("/login").status_code==404
        assert c.post("/auth/login",data={}).status_code==404
        assert "remote" not in c.get("/api/config").json()


def test_local_web_protects_against_cross_origin_browser_actions(isolated):
    with TestClient(gateway.external_app) as c:
        assert c.delete("/api/cache").status_code==200
        assert c.delete("/api/cache",headers={"Origin":"https://evil.example"}).status_code==403
        assert c.delete("/api/cache",headers={"Sec-Fetch-Site":"cross-site"}).status_code==403
        assert c.delete("/api/cache",headers={"Origin":"http://testserver"}).status_code==200


def test_migrate_legacy_login_credentials(isolated,monkeypatch,tmp_path):
    raw=main.defaults()
    raw["remote"]={"username":"old","password_salt":"abc","password_hash":"legacy"}
    monkeypatch.setattr(main,"STATE",tmp_path/"state.json")
    main.STATE.write_text(json.dumps(raw),encoding="utf8")
    updated=main.load()
    assert "remote" not in updated


def test_ingress_backend_works_with_supervisor_token(monkeypatch,isolated):
    # The backend receives authenticated requests forwarded by Supervisor.
    # A fixed Supervisor peer IP is not portable and must not block ingress.
    monkeypatch.setenv("SUPERVISOR_TOKEN","test-token")
    with TestClient(serve.ingress_app) as c:
        assert c.get("/api/config").status_code==200
    monkeypatch.delenv("SUPERVISOR_TOKEN")


def test_smart_settings_saved_and_range_validation(isolated):
    updated=main.update_config(isolated,{"smart":{"min_size":5.5,"max_size":11,
                "min_seeders":25,"quality":"1080","strict":False,"profile":"custom"}})
    assert updated["smart"]["min_size"]==5.5 and updated["smart"]["max_size"]==11
    assert updated["smart"]["min_seeders"]==25
    with pytest.raises(ValueError):
        main.update_config(isolated,{"smart":{"min_size":20,"max_size":10}})


def test_user_supplied_css_adapter(isolated):
    adapter={"path":"/search?q={query}","row":"tr.result","title":"a.title",
        "details":"a.title","magnet":"a.magnet","size":"td.size","seeders":"td.seed",
        "date":"td.date","encoding":"utf-8"}
    item={"id":"custom-own","kind":"custom","name":"My indexer","url":"https://example.org",
          "route":"inherit","enabled":True,"adapter":adapter}
    newer=main.update_config(isolated,{"direct_trackers":isolated["direct_trackers"]+[item]})
    assert len(newer["direct_trackers"])==6
    html='''<html><table><tr class="result"><td><a class="title" href="/torrent/1">Titanic 1080p</a>
    <a class="magnet" href="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567">Magnet</a>
    </td><td class="size">8 GB</td><td class="seed">100</td><td class="date">20.10.2026</td></tr></table></html>'''
    r=direct.parse_custom(html,"https://example.org",item)
    assert len(r)==1
    assert r[0]["seeders"]==100 and r[0]["size"]==8*1073741824
    assert r[0]["url"]=="https://example.org/torrent/1"
    assert r[0]["magnet"].startswith("magnet:?xt=")
    with pytest.raises(ValueError):
        main.update_config(isolated,{"direct_trackers":isolated["direct_trackers"]+[
            {**item,"adapter":{**adapter,"path":"//evil.example/{query}"}}]})


def test_pwa_resources_and_sanitised_configuration(isolated):
    with TestClient(main.app) as c:
        response=c.get("/manifest.webmanifest")
        assert response.status_code==200
        manifest=response.json()
        assert manifest["display"]=="standalone"
        assert manifest["start_url"]=="./"
        assert c.get("/icon.png").status_code==200
        response=c.get("/")
        assert "Умный поиск" in response.text
        assert "smartMin" in response.text
        assert "remotePass" not in response.text
        assert "CSS: название" in response.text


def test_private_tracker_cookies_never_return_via_api(monkeypatch,isolated):
    choices={t["id"]:t for t in isolated["direct_trackers"]}
    assert set(("rutracker","kinozal")).issubset(choices)
    assert not choices["rutracker"]["enabled"] and not choices["kinozal"]["enabled"]
    choices["rutracker"]["cookie"]="bb_session=sensitive-cookie"
    main.config=isolated
    with TestClient(main.app) as c:
        d=c.get("/api/config").json()
        t=next(x for x in d["direct_trackers"] if x["id"]=="rutracker")
        assert t["cookie_set"] and "cookie" not in t
        all_sources=c.get("/api/direct-trackers").json()
        assert "sensitive-cookie" not in json.dumps(all_sources)
    new=main.update_config(isolated,{"direct_trackers":[{**t,"cookie":""} for t in isolated["direct_trackers"]]})
    assert next(t for t in new["direct_trackers"] if t["id"]=="rutracker")["cookie"]=="bb_session=sensitive-cookie"
    headers=main.tracker_download_headers({"source_id":"rutracker"},"https://rutracker.org/forum/dl.php?t=123")
    assert headers["Cookie"]=="bb_session=sensitive-cookie"
    assert main.tracker_download_headers({"source_id":"rutracker"},"https://attacker.example/file.torrent")=={}


def test_rutracker_and_kinozal_html_parsers():
    rut='<html><tr><td><a class="tLink" href="viewtopic.php?t=55">Film 1080p</a></td><td class="tor-size">8 GB</td><td><b class="seedmed">48</b></td><td><a href="dl.php?t=55">Download</a></td></tr></html>'
    rz=direct.parse_rutracker(rut,"https://rutracker.org")
    assert len(rz)==1 and rz[0]["seeders"]==48 and rz[0]["size"]>8_000_000_000
    kin='<html><table><tr><td></td><td class="nam"><a href="/details.php?id=12">Film 4K</a></td><td></td><td>12 GB</td><td>100</td><td>0</td><td>20.10.2026</td></tr></table></html>'
    kz=direct.parse_kinozal(kin,"https://kinozal.me")
    assert len(kz)==1 and kz[0]["seeders"]==100 and "download.php" in kz[0]["download"]
