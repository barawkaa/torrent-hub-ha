"""Release-critical tests: ingress, independent LAN auth, PWA, smart filters, custom source."""
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


def test_standalone_rejects_unauthenticated_search(isolated):
    with TestClient(gateway.external_app) as client:
        r=client.get("/api/config",follow_redirects=False)
        assert r.status_code==401
        r=client.post("/api/send",json={"server_ids":["lg"],"id":"missing"})
        assert r.status_code==401
        r=client.get("/",follow_redirects=False)
        assert r.status_code==303 and r.headers["location"]=="/login"
        assert "пароль" in client.get("/login").text.lower()


def test_login_success_and_password_revocation(isolated):
    isolated2=main.update_config(isolated,{"remote":{"username":"operator","password":"strong-password-123"}})
    main.config=isolated2
    gateway.ATTEMPTS.clear()
    with TestClient(gateway.external_app) as client:
        assert client.post("/auth/login",data={"username":"operator","password":"wrong"}).status_code==401
        assert client.get("/api/config").status_code==401
        result=client.post("/auth/login",data={"username":"operator","password":"strong-password-123"},follow_redirects=False)
        assert result.status_code==303
        assert "httponly" in result.headers["set-cookie"].lower()
        assert "samesite=strict" in result.headers["set-cookie"].lower()
        response=client.get("/api/config")
        assert response.status_code==200
        body=response.json()
        assert body["remote"]["password_set"] is True
        assert "password_hash" not in body["remote"]
        assert "password_salt" not in body["remote"]
        assert "password" not in body["remote"]
        main.config=main.update_config(main.config,{"remote":{"password":"different-password-456"}})
        assert client.get("/api/config").status_code==401


def test_lan_port_only_allows_origins(monkeypatch,isolated):
    main.config=main.update_config(isolated,{"remote":{"password":"my-huge-long-password"}})
    with TestClient(gateway.external_app) as client:
        client.post("/auth/login",data={"username":"admin","password":"my-huge-long-password"})
        r=client.delete("/api/cache",headers={"Origin":"https://malicious.example"})
        assert r.status_code==403
        assert client.get("/api/storage").status_code==200


def test_ingress_only_from_supervisor(monkeypatch,isolated):
    monkeypatch.setenv("SUPERVISOR_TOKEN","test-token")
    with TestClient(serve.ingress_app) as c:
        assert c.get("/api/config").status_code==403
    monkeypatch.delenv("SUPERVISOR_TOKEN")
    with TestClient(serve.ingress_app) as c:
        assert c.get("/api/config").status_code==200


def test_smart_settings_saved_and_range_validation(isolated):
    updated=main.update_config(isolated,{"smart":{"min_size":5.5,"max_size":11,
                "min_seeders":25,"quality":"1080","strict":False,"profile":"custom"}})
    assert updated["smart"]["min_size"]==5.5 and updated["smart"]["max_size"]==11
    assert updated["smart"]["min_seeders"]==25
    with pytest.raises(ValueError):
        main.update_config(isolated,{"smart":{"min_size":20,"max_size":10}})
    with pytest.raises(ValueError):
        main.update_config(isolated,{"remote":{"password":"short"}})


def test_user_supplied_css_adapter(isolated):
    adapter={"path":"/search?q={query}","row":"tr.result","title":"a.title",
        "details":"a.title","magnet":"a.magnet","size":"td.size","seeders":"td.seed",
        "date":"td.date","encoding":"utf-8"}
    item={"id":"custom-own","kind":"custom","name":"My indexer","url":"https://example.org",
          "route":"inherit","enabled":True,"adapter":adapter}
    newer=main.update_config(isolated,{"direct_trackers":isolated["direct_trackers"]+[item]})
    assert len(newer["direct_trackers"])==4
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
        assert "remotePass" in response.text
        assert "CSS: название" in response.text
