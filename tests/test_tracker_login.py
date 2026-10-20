"""Private tracker login: correct POST forms, cookie handling, recovery, and no secret leakage."""
import asyncio
import json
import pathlib
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/"torrent-hub"/"app"))
import direct
import main

_ORIGINAL_CLIENT = httpx.AsyncClient


@pytest.mark.parametrize("kind,endpoint,fields,verify,marker",[
    ("rutracker","/forum/login.php",("login_username","login_password"),"/forum/tracker.php","logged-in-as-cap"),
    ("kinozal","/takelogin.php",("username","password"),"/my.php",'href="/logout.php?hash4u=123"')
])
def test_login_submits_credentials_and_verifies_actual_account(monkeypatch,kind,endpoint,fields,verify,marker):
    direct._AUTH_CACHE.clear()
    trace=[]
    base="https://"+("rutracker.org" if kind=="rutracker" else "kinozal.me")
    def handler(req):
        trace.append((req.method,str(req.url.path),req.content))
        if req.method=="POST" and req.url.path==endpoint:
            payload=req.content.decode("ascii")
            assert all(x+"=" in payload for x in fields)
            assert "private-pass" in payload
            return httpx.Response(302,headers={"Location":"/forum/index.php" if kind=="rutracker" else "/",
                            "Set-Cookie":"auth=token123; Path=/; HttpOnly"},request=req)
        if req.url.path==verify:
            return httpx.Response(200,text="<html>"+marker+"</html>",request=req)
        return httpx.Response(200,text="<html><form>Login</form></html>",request=req)
    def factory(*args,**kwargs):
        kwargs.pop("proxy",None)
        return _ORIGINAL_CLIENT(*args,transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(direct.httpx,"AsyncClient",factory)
    tracker={"id":kind,"kind":kind,"name":kind,"url":base,"route":"direct",
             "username":"movie-user","password":"private-pass"}
    cookie=asyncio.run(direct.login_tracker(tracker,{"mode":"direct"},force=True))
    assert "auth=token123" in cookie
    trace_count=len(trace)
    assert asyncio.run(direct.login_tracker(tracker,{"mode":"direct"}))==cookie
    assert len(trace)==trace_count
    direct.invalidate_tracker_session(kind)
    asyncio.run(direct.login_tracker(tracker,{"mode":"direct"}))
    assert len(trace)>trace_count


def test_reject_invalid_credentials_without_claiming_success(monkeypatch):
    direct._AUTH_CACHE.clear()
    def handler(req):
        return httpx.Response(200,text="<html>Гость. Введите пароль и войдите.</html>",request=req)
    def factory(*args,**kwargs):
        kwargs.pop("proxy",None)
        return _ORIGINAL_CLIENT(*args,transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(direct.httpx,"AsyncClient",factory)
    info={"id":"rutracker","kind":"rutracker","name":"RuTracker",
          "url":"https://rutracker.org","route":"direct","username":"wrong","password":"wrong"}
    with pytest.raises(direct.TrackerLoginError,match="не подтвердил вход"):
        asyncio.run(direct.login_tracker(info,{"mode":"direct"},force=True))
    assert not direct._AUTH_CACHE


def test_connect_persists_only_after_verified_login_and_redacts_credentials(monkeypatch,tmp_path):
    monkeypatch.setattr(main,"config",main.defaults())
    monkeypatch.setattr(main,"DATA",tmp_path)
    monkeypatch.setattr(main,"STATE",tmp_path/"state.json")
    async def success(tracker,proxy,**kwargs):
        assert tracker["username"]=="test-user" and tracker["password"]=="private-pass"
        return "bb_session=private-session"
    monkeypatch.setattr(main,"login_tracker",success)
    with TestClient(main.app) as client:
        r=client.post("/api/trackers/rutracker/connect",json={
            "username":"test-user","password":"private-pass","route":"direct","url":"https://rutracker.org"})
        assert r.status_code==200,r.text
        assert r.json()["connected"] is True
        found=client.get("/api/config").json()
        ru=next(t for t in found["direct_trackers"] if t["id"]=="rutracker")
        assert ru["enabled"] and ru["password_set"] and ru["username"]=="test-user"
        assert "password" not in ru and "cookie" not in ru
        assert "private-pass" not in json.dumps(found)
        assert "private-pass" not in client.get("/api/direct-trackers").text
        assert main.STATE.exists()
        assert (main.STATE.stat().st_mode & 0o777)==0o600
        # Password persists in private add-on storage for automatic re-login.
        persisted=json.loads(main.STATE.read_text())
        assert next(t for t in persisted["direct_trackers"] if t["id"]=="rutracker")["password"]=="private-pass"
        r=client.post("/api/trackers/rutracker/disconnect")
        assert r.status_code==200
        assert not next(t for t in main.config["direct_trackers"] if t["id"]=="rutracker")["password"]


def test_bad_login_does_not_save_password(monkeypatch,tmp_path):
    monkeypatch.setattr(main,"config",main.defaults())
    monkeypatch.setattr(main,"DATA",tmp_path)
    monkeypatch.setattr(main,"STATE",tmp_path/"state.json")
    async def reject(tracker,proxy,**kwargs):
        raise direct.TrackerLoginError("Сайт запросил капчу")
    monkeypatch.setattr(main,"login_tracker",reject)
    with TestClient(main.app) as client:
        r=client.post("/api/trackers/kinozal/connect",json={"username":"test-user","password":"bad-secret"})
        assert r.status_code==422
        assert "капчу" in r.json()["detail"]
        assert not main.config["direct_trackers"][-1]["password"]
        assert not main.STATE.exists()


def test_normal_settings_save_preserves_validated_password(monkeypatch):
    old=main.defaults()
    track=next(t for t in old["direct_trackers"] if t["id"]=="rutracker")
    track["username"]="test-user";track["password"]="private-pass";track["enabled"]=True
    masked=main.config
    monkeypatch.setattr(main,"config",old)
    imported=main.redacted()
    updated=main.update_config(old,{"direct_trackers":imported["direct_trackers"]})
    t=next(x for x in updated["direct_trackers"] if x["id"]=="rutracker")
    assert t["password"]=="private-pass" and t["username"]=="test-user"
