"""Live torrent tracker adapters: no FileDB, no Prowlarr, no third-party search API.

Selectors and request shapes are informed by the public Jackett definitions
(rutor.yml, megapeer.yml, noname-club.yml). Trackers may change their markup;
such changes are surfaced as explicit provider errors.
"""
from __future__ import annotations
import re
from datetime import datetime
from urllib.parse import quote, urljoin, quote_from_bytes
from bs4 import BeautifulSoup
from core import fetch, result_of, magnet_hash, external_url_guard

TRACKERS = {
    "rutor": {"name":"RuTor", "url":"https://rutor.info"},
    "megapeer": {"name":"MegaPeer", "url":"https://megapeer.vip"},
    "nnmclub": {"name":"NNM-Club", "url":"https://nnmclub.to"},
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


PARSERS={"rutor":parse_rutor,"megapeer":parse_megapeer,"nnmclub":parse_nnmclub}

async def search_tracker(tracker:dict,proxy:dict,query:str)->list[dict]:
    kind=str(tracker.get("kind","")).lower()
    if kind not in PARSERS and kind!="custom":raise ValueError("Неизвестный прямой трекер: "+kind)
    base=str(tracker.get("url","") or TRACKERS.get(kind,{}).get("url","")).rstrip("/")
    external_url_guard(base)
    route=tracker.get("route","inherit")
    if kind=="custom":
        pattern=str(tracker.get("adapter",{}).get("path",""))
        if not pattern.startswith("/") or pattern.startswith("//") or "{query}" not in pattern:
            raise ValueError("Адрес поиска: путь должен начинаться с / и содержать {query}")
        url=base+pattern.replace("{query}",quote(query.strip(),safe=""))
        response=await fetch(proxy,route,url,timeout=24,headers=COMMON_HEADERS)
        html=response.content.decode(tracker.get("adapter",{}).get("encoding","utf-8"),errors="replace")
    elif kind=="rutor":
        url=base+"/search/0/0/100/0/"+quote(query.strip(),safe="")
        response=await fetch(proxy,route,url,timeout=24,headers=COMMON_HEADERS)
        html=response.content.decode(response.encoding or "utf-8",errors="replace")
    elif kind=="megapeer":
        response=await fetch(proxy,route,base+"/browse.php?search="+quote_from_bytes(query.encode("cp1251"))+"&cat=0&stype=0&sort=0&ascdesc=0",
                            timeout=24,headers=COMMON_HEADERS)
        html=response.content.decode("cp1251",errors="replace")
    else:
        response=await fetch(proxy,route,base+"/forum/tracker.php",method="POST",content=__import__("urllib.parse",fromlist=["urlencode"]).urlencode(
            {"f[]":"-1","o":"10","s":"0","tm":"-1","shf":"1","sha":"1","ta":"-1","sns":"-1","sds":"4","nm":query,"submit":"Поиск"},encoding="cp1251").encode("ascii"),
                            headers={**COMMON_HEADERS,"Content-Type":"application/x-www-form-urlencoded"},timeout=24)
        html=response.content.decode("cp1251",errors="replace")
    items=parse_custom(html,base,tracker) if kind=="custom" else PARSERS[kind](html,base)
    for item in items:
        item["source_id"]=tracker.get("id",kind)
        item["route"]=route
    return items[:150]
