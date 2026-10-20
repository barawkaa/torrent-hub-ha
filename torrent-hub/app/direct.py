"""Live torrent tracker adapters: no FileDB, no Prowlarr, no third-party search API.

Selectors and request shapes are informed by the public Jackett definitions
(rutor.yml, megapeer.yml, noname-club.yml). Trackers may change their markup;
such changes are surfaced as explicit provider errors.
"""
from __future__ import annotations
import re
from datetime import datetime
import asyncio
import hashlib
import time
from urllib.parse import urlencode, urlsplit, urljoin, quote, quote_from_bytes
import httpx
from bs4 import BeautifulSoup
from core import fetch, result_of, magnet_hash, external_url_guard, proxy_address, choose_routes

TRACKERS = {
    "rutor": {"name":"RuTor", "url":"https://rutor.info"},
    "megapeer": {"name":"MegaPeer", "url":"https://megapeer.vip"},
    "nnmclub": {"name":"NNM-Club", "url":"https://nnmclub.to"},
    "rutracker": {"name":"RuTracker", "url":"https://rutracker.org", "requires_auth":True},
    "kinozal": {"name":"Kinozal", "url":"https://kinozal.me", "requires_auth":True},
}
SIZE_UNITS={"b":1,"kb":1024,"mb":1048576,"gb":1073741824,"tb":1099511627776,
            "байт":1,"кб":1024,"мб":1048576,"гб":1073741824,"тб":1099511627776}
SIZE_RE=re.compile(r"(\d+(?:[.,]\d+)?)\s*(tb|gb|mb|kb|b|тб|гб|мб|кб|байт)\b",re.I)
COMMON_HEADERS={"User-Agent":"Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
                "Accept":"text/html,application/xhtml+xml,*/*"}

def human_size(s: str) -> int:
    m=SIZE_RE.search(s.replace("\xa0"," ").replace("\u202f"," "))
    return int(float(m.group(1).replace(",","."))*SIZE_UNITS[m.group(2).lower()]) if m else 0

def number(s: str) -> int:
    v=re.search(r"\d+",s.replace(" ","").replace("\xa0",""))
    return int(v.group(0)) if v else 0

def date_iso(s: str)->str:
    s=" ".join(s.replace("\xa0"," ").split())
    ru={"янв":"Jan","фев":"Feb","мар":"Mar","апр":"Apr","май":"May","мая":"May","июн":"Jun",
        "июл":"Jul","авг":"Aug","сен":"Sep","окт":"Oct","ноя":"Nov","дек":"Dec"}
    for ru_word,en in ru.items():
        s=re.sub(r"\b"+ru_word+r"\b",en,s,flags=re.I)
    for fmt in ("%d %b %y","%d %b %Y","%d.%m.%Y","%d.%m.%y"):
        try:return datetime.strptime(s,fmt).date().isoformat()
        except ValueError:pass
    if s.isdigit() and len(s)>=9:
        try:return datetime.fromtimestamp(int(s)).date().isoformat()
        except (OverflowError,ValueError,OSError):pass
    return ""

def make_row(title,origin,details,magnet,download,size,seeders,date):
    obj=result_of({"title":title,"magnet":magnet,"downloadUrl":download,
                  "size":size,"seeders":seeders,"date":date,"infoUrl":details},origin)
    obj["source_id"]=origin.lower().replace("-","")
    return obj

def ensure_document(html: str,tracker:str):
    lowered=html.lower()
    if any(marker in lowered for marker in ("just a moment...","cf-challenge","checking your browser","captcha challenge")):
        raise ValueError(tracker+": сайт требует браузерную проверку/капчу")
    if "<html" not in lowered and "<table" not in lowered:
        raise ValueError(tracker+": получен не HTML каталога (возможная блокировка)")

def parse_rutor(html:str,base:str)->list[dict]:
    ensure_document(html,"RuTor")
    soup=BeautifulSoup(html,"html.parser")
    found=[]
    for tr in soup.select("tr"):
        magnet_el=tr.select_one('a[href^="magnet:?xt="]')
        title_el=tr.select_one('a[href^="/torrent/"]')
        if title_el is None or magnet_el is None:continue
        cells=tr.find_all("td")
        size=next((human_size(td.get_text(" ",strip=True)) for td in cells if SIZE_RE.search(td.get_text(" ",strip=True))),0)
        seeds_el=tr.select_one("span.green")
        torrent_el=tr.select_one("a.downgif")
        found.append(make_row(title_el.get_text(" ",strip=True),"RuTor",
                              urljoin(base,title_el.get("href","")),magnet_el.get("href",""),
                              urljoin(base,torrent_el.get("href","")) if torrent_el else "",
                              size,number(seeds_el.get_text(" ",strip=True)) if seeds_el else 0,
                              date_iso(cells[0].get_text(" ",strip=True)) if cells else ""))
    return found

def parse_megapeer(html:str,base:str)->list[dict]:
    ensure_document(html,"MegaPeer")
    soup=BeautifulSoup(html,"html.parser")
    result=[]
    for row in soup.select("tr.table_fon"):
        title=row.select_one('a[href^="/torrent/"]')
        if title is None:continue
        down=row.select_one('a[href^="/download/"]')
        cells=row.select("td")
        if len(cells)<3:continue
        seeds=row.select_one("td:last-child font:first-of-type")
        result.append(make_row(title.get_text(" ",strip=True),"MegaPeer",
            urljoin(base,title.get("href","")),"",
            urljoin(base,down.get("href","")) if down else "",
            human_size(cells[-2].get_text(" ",strip=True)),
            number(seeds.get_text(" ",strip=True)) if seeds else 0,
            date_iso(cells[0].get_text(" ",strip=True))))
    return result

def parse_nnmclub(html:str,base:str)->list[dict]:
    ensure_document(html,"NNM-Club")
    soup=BeautifulSoup(html,"html.parser")
    result=[]
    for tr in soup.select("table.forumline.tablesorter tbody tr"):
        title=tr.select_one('a[href^="viewtopic.php?t="]')
        down=tr.select_one('a[href^="download.php?id="]')
        if not title or not down:continue
        size=tr.select_one("td:nth-child(6) > u")
        seeds=tr.select_one("td.seedmed > b")
        date=tr.select_one("td:last-child > u")
        result.append(make_row(title.get_text(" ",strip=True),"NNM-Club",
          urljoin(base+"/forum/",title.get("href","")),"",
          urljoin(base+"/forum/",down.get("href","")),
          human_size(size.get_text(" ",strip=True)) if size else 0,
          number(seeds.get_text(" ",strip=True)) if seeds else 0,
          date_iso(date.get_text(" ",strip=True)) if date else ""))
    return result


def parse_rutracker(html:str,base:str)->list[dict]:
    ensure_document(html,"RuTracker")
    soup=BeautifulSoup(html,"html.parser")
    results=[]
    for row in soup.select("tr"):
        title=row.select_one("a.tLink")
        if title is None:continue
        torrent=row.select_one('a[href*="dl.php?t="]')
        size=row.select_one("td.tor-size")
        seeds=row.select_one("b.seedmed")
        results.append(make_row(title.get_text(" ",strip=True),"RuTracker",
          urljoin(base+"/forum/",title.get("href","")),"",
          urljoin(base+"/forum/",torrent.get("href","")) if torrent else "",
          human_size(size.get_text(" ",strip=True)) if size else 0,
          number(seeds.get_text(" ",strip=True)) if seeds else 0,""))
    return results


def parse_kinozal(html:str,base:str)->list[dict]:
    ensure_document(html,"Kinozal")
    soup=BeautifulSoup(html,"html.parser")
    results=[]
    for row in soup.select("tr"):
        title=row.select_one('td.nam a[href*="details.php?"]')
        if title is None:continue
        cells=row.select("td")
        info=title.get("href","")
        results.append(make_row(title.get_text(" ",strip=True),"Kinozal",
          urljoin(base,info),"",urljoin(base,info.replace("details.php?","download.php?")),
          human_size(cells[3].get_text(" ",strip=True)) if len(cells)>3 else 0,
          number(cells[4].get_text(" ",strip=True)) if len(cells)>4 else 0,
          date_iso(cells[6].get_text(" ",strip=True)) if len(cells)>6 else ""))
    return results


def parse_custom(html: str, base: str, tracker: dict) -> list[dict]:
    """Declarative CSS selectors; no user-supplied Python/JS code is executed."""
    ensure_document(html, tracker.get("name","Custom"))
    spec = tracker.get("adapter",{})
    soup = BeautifulSoup(html[:2_000_000],"html.parser")
    try:
        rows = soup.select(spec["row"])
    except Exception as exc:
        raise ValueError("Ошибка CSS-селектора строк: "+str(exc)[:80])
    found = []
    def extract(row,key,attr=None):
        selector = spec.get(key,"")
        if not selector:
            return ""
        element = row.select_one(selector)
        if not element:
            return ""
        return (element.get(attr) or "") if attr else element.get_text(" ",strip=True)
    for row in rows[:150]:
        try:
            title=extract(row,"title")
            if not title: continue
            detail=extract(row,"details","href")
            magnet=extract(row,"magnet","href")
            download=extract(row,"download","href")
            url_download=urljoin(base,download) if download and not download.startswith("magnet:") else download
            found.append(make_row(title,tracker["name"],urljoin(base,detail) if detail else "",
                                  magnet if magnet.startswith("magnet:?") else "",
                                  url_download,human_size(extract(row,"size")),
                                  number(extract(row,"seeders")),date_iso(extract(row,"date"))))
        except (ValueError,AttributeError):
            continue
    return found



# Session credentials never leave the server. Short-lived cookies live in RAM.
_AUTH_CACHE = {}
_AUTH_LOCKS = {}

class TrackerLoginError(ValueError):
    pass

def invalidate_tracker_session(tracker_id: str):
    for key in tuple(_AUTH_CACHE):
        if key[0] == tracker_id:
            _AUTH_CACHE.pop(key, None)

def _cache_key(tracker: dict, password: str) -> tuple:
    digest=hashlib.sha256(password.encode("utf8")).hexdigest()
    return (str(tracker.get("id")),str(tracker.get("url")),str(tracker.get("username","")),digest,
            str(tracker.get("route","inherit")))

def _check_blocked(text: str):
    page=text.lower()
    if any(k in page for k in ("cf-challenge","just a moment","checking your browser","cloudflare ray id","error 1020")):
        raise TrackerLoginError("Сайт требует проверку Cloudflare. Попробуй другое зеркало или маршрут через прокси.")
    if any(k in page for k in ("cap_sid","captcha","капч","код с картинки","g-recaptcha")):
        raise TrackerLoginError("Сайт запросил капчу. Автоматический вход сейчас невозможен.")

async def _same_host_request(client, method, url, base, **kwargs):
    host=urlsplit(base).hostname
    for _ in range(5):
        if urlsplit(url).hostname != host or urlsplit(url).scheme not in ("https","http"):
            raise TrackerLoginError("Трекер перенаправил авторизацию на другой домен: проверь адрес зеркала")
        response=await client.request(method,url,follow_redirects=False,**kwargs)
        if response.status_code not in (301,302,303,307,308):
            response.raise_for_status()
            return response
        location=response.headers.get("location")
        if not location:
            raise TrackerLoginError("Ошибка входа: перенаправление без адреса")
        url=urljoin(str(response.url),location)
        if response.status_code in (301,302,303):
            method="GET";kwargs.pop("content",None);kwargs.pop("data",None)
    raise TrackerLoginError("Слишком много перенаправлений при входе")

def _is_logged_in(kind: str, page: str) -> bool:
    lower=page.lower()
    if kind=="kinozal":
        return "logout.php?hash4u=" in lower or "logout.php?hash=" in lower
    # RuTracker exposes an account marker when signed in.
    if "logged-in-as-cap" in lower:
        return True
    return bool(re.search(r"\bIS_GUEST\s*:\s*(?:!!\s*['\"]0['\"]|false|0)\b",page,re.I))

def _cookie_header(client, base:str) -> str:
    host=urlsplit(base).hostname or ""
    result=[]
    for cookie in client.cookies.jar:
        domain=cookie.domain.lstrip(".")
        if host==domain or host.endswith("."+domain):
            result.append(cookie.name+"="+cookie.value)
    return "; ".join(result)

async def login_tracker(tracker:dict, proxy:dict, *, force:bool=False) -> str:
    """Authenticate via actual tracker POST form and verify account before caching.

    Never assume that a successful HTTP response or presence of a cookie means login.
    """
    kind=tracker.get("kind")
    if kind not in ("rutracker","kinozal"):
        raise TrackerLoginError("Для этого источника авторизация не требуется")
    base=str(tracker.get("url","")).rstrip("/")
    external_url_guard(base)
    user=str(tracker.get("username","")).strip()
    pwd=str(tracker.get("password",""))
    if not user or not pwd:
        raise TrackerLoginError("Введи логин и пароль от "+tracker.get("name",kind))
    key=_cache_key(tracker,pwd)
    now=time.monotonic()
    if not force and key in _AUTH_CACHE and now-_AUTH_CACHE[key][0]<600:
        return _AUTH_CACHE[key][1]
    lock=_AUTH_LOCKS.setdefault(tracker.get("id",kind),asyncio.Lock())
    async with lock:
        if not force and key in _AUTH_CACHE and time.monotonic()-_AUTH_CACHE[key][0]<600:
            return _AUTH_CACHE[key][1]
        errors=[]
        for route in choose_routes(proxy,tracker.get("route","inherit")):
            px=proxy_address(proxy) if route=="proxy" else None
            try:
                async with httpx.AsyncClient(proxy=px,timeout=httpx.Timeout(25,connect=8),
                                             trust_env=False,follow_redirects=False,
                                             headers=COMMON_HEADERS) as client:
                    if kind=="rutracker":
                        login_url=base+"/forum/login.php"
                        check_url=base+"/forum/tracker.php"
                        params={"login_username":user,"login_password":pwd,"login":"Вход"}
                    else:
                        login_url=base+"/takelogin.php"
                        check_url=base+"/my.php"
                        params={"username":user,"password":pwd}
                    # GET establishes any site cookies before submitting credentials.
                    landing=await _same_host_request(client,"GET",base+("/forum/login.php" if kind=="rutracker" else "/login.php"),base)
                    _check_blocked(landing.text)
                    if landing.status_code in (401,403):
                        raise TrackerLoginError("Сайт запретил вход (HTTP "+str(landing.status_code)+")")
                    posted=await _same_host_request(client,"POST",login_url,base,
                        content=urlencode(params,encoding="cp1251").encode("ascii"),
                        headers={"Content-Type":"application/x-www-form-urlencoded"})
                    _check_blocked(posted.text)
                    checked=await _same_host_request(client,"GET",check_url,base)
                    _check_blocked(checked.text)
                    if not _is_logged_in(kind,checked.text):
                        raise TrackerLoginError("Сайт не подтвердил вход. Проверь логин/пароль; возможно, требуется капча.")
                    cookies=_cookie_header(client,base)
                    if not cookies:
                        raise TrackerLoginError("Сайт подтвердил вход, но не выдал Cookie сессии")
                    _AUTH_CACHE[key]=(time.monotonic(),cookies)
                    return cookies
            except TrackerLoginError as exc:
                errors.append(str(exc))
                if "капчу" in str(exc) or "Cloudflare" in str(exc) or "не подтвердил вход" in str(exc):
                    break
            except (httpx.HTTPError,ValueError) as exc:
                errors.append("Соединение: "+type(exc).__name__+" — "+str(exc)[:85])
        raise TrackerLoginError("; ".join(errors) or "Не удалось авторизоваться")


PARSERS={"rutor":parse_rutor,"megapeer":parse_megapeer,"nnmclub":parse_nnmclub,"rutracker":parse_rutracker,"kinozal":parse_kinozal}

async def search_tracker(tracker:dict,proxy:dict,query:str)->list[dict]:
    kind=str(tracker.get("kind","")).lower()
    if kind not in PARSERS and kind!="custom":raise ValueError("Неизвестный прямой трекер: "+kind)
    base=str(tracker.get("url","") or TRACKERS.get(kind,{}).get("url","")).rstrip("/")
    external_url_guard(base)
    route=tracker.get("route","inherit")
    cookie=str(tracker.get("cookie","") or "").strip()
    if kind in ("rutracker","kinozal") and tracker.get("username") and tracker.get("password"):
        cookie=await login_tracker(tracker,proxy)
    if kind in ("rutracker","kinozal") and not cookie:
        raise TrackerLoginError("Сначала нажми «Подключить» и введи логин и пароль")
    auth_headers={**COMMON_HEADERS,**({"Cookie":cookie} if cookie else {})}
    if kind=="custom":
        pattern=str(tracker.get("adapter",{}).get("path",""))
        if not pattern.startswith("/") or pattern.startswith("//") or "{query}" not in pattern:
            raise ValueError("Адрес поиска: путь должен начинаться с / и содержать {query}")
        url=base+pattern.replace("{query}",quote_from_bytes(query.strip().encode(tracker.get("adapter",{}).get("encoding") or "utf-8")))
        response=await fetch(proxy,route,url,timeout=24,headers=auth_headers)
        html=response.content.decode(tracker.get("adapter",{}).get("encoding","utf-8"),errors="replace")
    elif kind=="rutor":
        url=base+"/search/0/0/100/0/"+quote(query.strip(),safe="")
        response=await fetch(proxy,route,url,timeout=24,headers=auth_headers)
        html=response.content.decode(response.encoding or "utf-8",errors="replace")
    elif kind=="megapeer":
        response=await fetch(proxy,route,base+"/browse.php?search="+quote_from_bytes(query.encode("cp1251"))+"&cat=0&stype=0&sort=0&ascdesc=0",
                            timeout=24,headers=auth_headers)
        html=response.content.decode("cp1251",errors="replace")
    elif kind=="rutracker":
        url=base+"/forum/tracker.php?nm="+quote_from_bytes(query.encode("cp1251"))
        response=await fetch(proxy,route,url,timeout=24,headers=auth_headers)
        html=response.content.decode("cp1251",errors="replace")
    elif kind=="kinozal":
        url=base+"/browse.php?s="+quote_from_bytes(query.encode("cp1251"))+"&g=0&c=0&v=0&d=0&w=0&t=0&f=0"
        response=await fetch(proxy,route,url,timeout=24,headers=auth_headers)
        html=response.content.decode("cp1251",errors="replace")
    else:
        response=await fetch(proxy,route,base+"/forum/tracker.php",method="POST",content=__import__("urllib.parse",fromlist=["urlencode"]).urlencode(
            {"f[]":"-1","o":"10","s":"0","tm":"-1","shf":"1","sha":"1","ta":"-1","sns":"-1","sds":"4","nm":query,"submit":"Поиск"},encoding="cp1251").encode("ascii"),
                            headers={**auth_headers,"Content-Type":"application/x-www-form-urlencoded"},timeout=24)
        html=response.content.decode("cp1251",errors="replace")
    if kind in ("rutracker","kinozal") and tracker.get("username") and tracker.get("password"):
        # Authenticated tracker sometimes silently returns the login form instead of results.
        if 'login_username' in html or 'name="password"' in html and 'takelogin.php' in html:
            invalidate_tracker_session(tracker.get("id",kind))
            cookie=await login_tracker(tracker,proxy,force=True)
            response=await fetch(proxy,route,url,timeout=24,headers={**COMMON_HEADERS,"Cookie":cookie})
            html=response.content.decode("cp1251",errors="replace")
    items=parse_custom(html,base,tracker) if kind=="custom" else PARSERS[kind](html,base)
    for item in items:
        item["source_id"]=tracker.get("id",kind)
        item["route"]=route
    return items[:150]
