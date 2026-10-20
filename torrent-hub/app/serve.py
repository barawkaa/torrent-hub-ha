"""Run Ingress and authenticated remote listener sharing one Python backend."""
import asyncio
import uvicorn
from main import app
from gateway import external_app

async def run():
    ingress = uvicorn.Server(uvicorn.Config(app,host="0.0.0.0",port=8765,log_level="info",proxy_headers=False))
    remote = uvicorn.Server(uvicorn.Config(external_app,host="0.0.0.0",port=8766,log_level="info",proxy_headers=False))
    await asyncio.gather(ingress.serve(),remote.serve())

if __name__=="__main__":
    asyncio.run(run())
