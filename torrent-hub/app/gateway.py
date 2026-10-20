"""Authenticated standalone gateway for Torrent Hub.

Ingress keeps its independent Home Assistant session on 8765.
This app ONLY exposes :8766 after a separate Torrent Hub password is set.
Both listeners share the SAME backend module and search state in one process.
"""
from __future__ import annotations
import hashlib
import hmac
import html
import json
import logging
import os
import pathlib
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import main as backend

log = logging.getLogger("torrent_hub.gateway")
external_app = FastAPI(title="Torrent Hub External", docs_url=None, redoc_url=None, openapi_url=None)
COOKIE = "torrent_hub_session"
DURATION = 7 * 24 * 3600

def configured():
    access = backend.config.get("remote", {})
    return bool(access.get("password_hash") and access.get("password_salt"))

def session_key():
    path = backend.DATA / ".external-session-key"
    if path.exists():
        raw = path.read_bytes()
        if len(raw) == 32:
            return raw
        raise RuntimeError("Incorrect gateway session secret length")
    backend.DATA.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return path.read_bytes()
    raw = secrets.token_bytes(32)
    with os.fdopen(fd, "wb") as f:
        f.write(raw)
    return raw

def token(expiry: int) -> str:
    stored = backend.config["remote"]["password_hash"]
    payload = f"v1:{expiry}:{stored}".encode()
    sig = hmac.new(session_key(), payload, hashlib.sha256).hexdigest()
    return f"{expiry}.{sig}"

def authenticated(request: Request) -> bool:
    if not configured():
        return False
    raw = request.cookies.get(COOKIE, "")
    try:
        when, signature = raw.split(".", 1)
        when = int(when)
        if when < time.time() or when > time.time() + DURATION + 60 or len(signature) != 64:
            return False
        return hmac.compare_digest(token(when).split(".")[1], signature)
    except (ValueError, TypeError):
        return False

def same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    return not origin or urlsplit(origin).netloc.lower() == request.headers.get("host","").lower()

@external_app.middleware("http")
async def access_control(request: Request, call_next):
    path = request.url.path
    is_login = path in ("/login", "/auth/login", "/icon.png", "/manifest.webmanifest")
    if not is_login and not authenticated(request):
        if request.method == "GET" and not path.startswith("/api/"):
            response = RedirectResponse("/login", status_code=303)
        else:
            response = JSONResponse({"detail": "Необходим вход в Torrent Hub"}, status_code=401)
    elif request.method not in ("GET", "HEAD", "OPTIONS") and not same_origin(request):
        response = JSONResponse({"detail": "Запрос отклонён: другой источник"}, status_code=403)
    else:
        response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

def login_page(note: str = ""):
    if not configured():
        note = "Удалённый вход пока не включён. Задайте пароль на вкладке «Доступ» внутри Home Assistant."
    return HTMLResponse(f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Torrent Hub — вход</title><link rel="icon" href="icon.png">
<style>*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1422;color:#ecf7ff;font:16px system-ui}}
main{{width:min(94vw,390px);padding:28px;border:1px solid #2d506d;border-radius:20px;background:#142338}}
h1{{margin-top:0}}label{{display:block;margin-top:16px}}input{{width:100%;padding:14px;background:#09192d;border:1px solid #42617b;color:#fff;border-radius:9px;font-size:16px}}
button{{margin-top:22px;width:100%;padding:13px;background:#00a69e;color:#fff;border:0;border-radius:9px;font-size:16px;font-weight:700}}
p{{color:#b6c8d8}}</style></head><body><main><h1>Torrent Hub</h1><p>Защищённый вход</p>
<form method="post" action="auth/login"><label>Логин<input name="username" autocomplete="username" required></label>
<label>Пароль<input type="password" name="password" autocomplete="current-password" required></label>
<button type="submit">Войти</button></form><p role="alert">{html.escape(note)}</p></main></body></html>""")

@external_app.get("/login")
async def login():
    return login_page()

# Keep a small rolling login limiter to discourage online brute force.
ATTEMPTS = {}

@external_app.post("/auth/login")
async def do_login(request: Request):
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    recent = [x for x in ATTEMPTS.get(key, []) if now - x < 300]
    if len(recent) >= 10:
        return HTMLResponse(login_page("Слишком много попыток. Подождите пять минут.").body, status_code=429)
    raw = (await request.body())[:4096].decode("utf-8", errors="replace")
    data = parse_qs(raw, keep_blank_values=True)
    username = data.get("username", [""])[0]
    password = data.get("password", [""])[0]
    account = backend.config.get("remote", {})
    okay = False
    if configured() and hmac.compare_digest(username, account.get("username", "")):
        try:
            material = hashlib.scrypt(password.encode(),salt=bytes.fromhex(account["password_salt"]),
                                     n=16384,r=8,p=1,dklen=32).hex()
            okay = hmac.compare_digest(material, account["password_hash"])
        except (KeyError, ValueError):
            pass
    if not okay:
        ATTEMPTS[key] = recent + [now]
        return HTMLResponse(login_page("Неверный логин или пароль.").body, status_code=401)
    ATTEMPTS.pop(key, None)
    expiry = int(time.time()) + DURATION
    result = RedirectResponse("/",status_code=303)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto","").lower()=="https"
    result.set_cookie(COOKIE,token(expiry),max_age=DURATION,httponly=True,secure=secure,samesite="strict",path="/")
    return result

@external_app.post("/auth/logout")
async def logout():
    response = RedirectResponse("/login",status_code=303)
    response.delete_cookie(COOKIE,path="/")
    return response

external_app.mount("/", backend.app)
