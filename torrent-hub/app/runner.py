"""Supervise bundled indexer engine + Home Assistant web UI in one add-on container."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

running=True
children=[]


def stop(_sig,_frame):
    global running
    running=False


def launch_engine():
    data=Path('/data/torrent-hub');data.mkdir(parents=True,exist_ok=True)
    output=open(data/'engine.log','ab',buffering=0)
    child=subprocess.Popen(['/opt/prowlarr/Prowlarr','-nobrowser','-data=/data/prowlarr'],stdout=output,stderr=subprocess.STDOUT)
    children.append((child,output))
    return child


def main():
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    Path('/data/prowlarr').mkdir(parents=True,exist_ok=True)
    engine=launch_engine()
    api=subprocess.Popen([sys.executable,'/app/main.py'])
    children.append((api,None))
    last_restart=0
    try:
        while running:
            if api.poll() is not None:
                print('Torrent Hub API stopped:',api.returncode,flush=True)
                break
            if engine.poll() is not None and time.time()-last_restart>15:
                print('Prowlarr stopped; restarting after 10 seconds',flush=True)
                time.sleep(10)
                if not running:break
                engine=launch_engine()
                last_restart=time.time()
            time.sleep(2)
    finally:
        for process,_ in children:
            if process.poll() is None:process.terminate()
        for process,handle in children:
            try:process.wait(timeout=12)
            except subprocess.TimeoutExpired:process.kill()
            if handle:handle.close()
    return api.returncode or 0


if __name__=='__main__':
    sys.exit(main())
