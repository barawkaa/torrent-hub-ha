"""Run Ingress and authenticated remote listener sharing one Python backend."""
import asyncio
import uvicorn
from main import app
from fastapi import FastAPI
from contextlib import asynccontextmanager
from gateway import external_app

@asynccontextmanager
async def ingress_lifespan(_):
    async with app.router.lifespan_context(app):
        yield

# The ingress backend is not mapped to a host port; HA Supervisor is
# responsible for authentication and forwarding. Never pin its peer IP.
ingress_app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=ingress_lifespan)
ingress_app.mount('/',app)

async def run():
    ingress = uvicorn.Server(uvicorn.Config(ingress_app,host="0.0.0.0",port=8765,log_level="info",proxy_headers=False))
    remote = uvicorn.Server(uvicorn.Config(external_app,host="0.0.0.0",port=8766,log_level="info",proxy_headers=False))
    await asyncio.gather(ingress.serve(),remote.serve())

if __name__=="__main__":
    asyncio.run(run())
