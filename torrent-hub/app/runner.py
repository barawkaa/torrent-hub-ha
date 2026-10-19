"""Supervise bundled indexer engine + Home Assistant web UI in one add-on container."""
import os
import signal
import subprocess
import shutil
import hashlib
import json
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


def install_bundled_definitions(bundled=Path('/app/definitions'), destination=Path('/data/prowlarr/Definitions/Custom')):
    """Install versioned definitions; preserve user overrides and support upgrades."""
    destination.mkdir(parents=True, exist_ok=True)
    manifest=destination/'.torrenthub-managed.json'
    try: managed=json.loads(manifest.read_text('utf8'))
    except (FileNotFoundError,ValueError):managed={}
    updated=[]
    for filename in ('rutor.yml', 'noname-club.yml', 'megapeer.yml', 'kinozal.yml', 'rustorka.yml'):
        source=bundled/filename
        target=destination/filename
        if not source.is_file():continue
        source_sha=hashlib.sha256(source.read_bytes()).hexdigest()
        current_sha=hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else ''
        if not target.exists() or (current_sha==managed.get(filename) and current_sha!=source_sha):
            shutil.copyfile(source,target)
            managed[filename]=source_sha
            updated.append(filename)
    manifest.write_text(json.dumps(managed,indent=2),'utf8')
    return updated


def main():
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    Path('/data/prowlarr').mkdir(parents=True,exist_ok=True)
    install_bundled_definitions()
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
