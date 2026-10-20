"""Run Ingress and authenticated remote listener sharing one Python backend."""
import asyncio
import uvicorn
from main import app
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
from gateway import external_app

ingress_app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

@ingress_app.middleware('http')
async def supervisor_only(request:Request,call_next):
    # In HA production this listener is exclusively for the Supervisor proxy.
    if (os.getenv('SUPERVISOR_TOKEN') or os.getenv('HASSIO_TOKEN')) and request.client and request.client.host != '172.30.32.2':
        return JSONResponse({'detail':'Ingress доступен только через Home Assistant'},status_code=403)
    return await call_next(request)

ingress_app.mount('/',app)

async def run():
    ingress = uvicorn.Server(uvicorn.Config(ingress_app,host="0.0.0.0",port=8765,log_level="info",proxy_headers=False))
    remote = uvicorn.Server(uvicorn.Config(external_app,host="0.0.0.0",port=8766,log_level="info",proxy_headers=False))
    await asyncio.gather(ingress.serve(),remote.serve())

if __name__=="__main__":
    asyncio.run(run())
