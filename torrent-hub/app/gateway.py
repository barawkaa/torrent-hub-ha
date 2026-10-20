"""Local web interface for port 8766, sharing the Torrent Hub backend."""
from urllib.parse import urlsplit
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import main as backend

external_app=FastAPI(title="Torrent Hub Local",docs_url=None,redoc_url=None,openapi_url=None)

@external_app.middleware("http")
async def local_headers(request:Request,call_next):
    if request.method not in ("GET","HEAD","OPTIONS"):
        origin=request.headers.get("origin")
        if request.headers.get("sec-fetch-site","").lower()=="cross-site":
            return JSONResponse({"detail":"Межсайтовый запрос запрещён"},status_code=403)
        if origin:
            host=request.headers.get("host","").lower()
            forwarded=request.headers.get("x-forwarded-host","").split(",")[0].strip().lower()
            actual=urlsplit(origin).netloc.lower()
            if not actual or actual not in (host,forwarded):
                return JSONResponse({"detail":"Межсайтовый запрос запрещён"},status_code=403)
    resp=await call_next(request)
    resp.headers["Cache-Control"]="no-store"
    resp.headers["X-Frame-Options"]="DENY"
    resp.headers["X-Content-Type-Options"]="nosniff"
    return resp

external_app.mount("/",backend.app)
