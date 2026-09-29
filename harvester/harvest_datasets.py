"""
Downloads the Mobility Tracking data files (Excel/CSV) that DTM publishes online and
saves them to data/raw/online/ so build_baseline.py can use them:

  sources  – 1. HDX (data.humdata.org) through its API: every IOM DTM Nigeria mobility-tracking dataset
               (baseline, site, location assessments, master lists, North East and North Central/North West)
             2. every dataset page on dtm.iom.int/nigeria?page=0,1,2… found by harvest_web.py
  files    – ne_mobility_online_r<round>_<slug>.xlsx / nwnc_mobility_online_r<round>_<slug>.xlsx
  manifest – data/raw/online/manifest.json (title, page, file link, round, region, download date)

Online files are preferred over the attached files for the same round, as long as their
totals agree (within 5%) with the attached file or the published figure (see build_baseline.py).
Some DTM datasets are "request access" only; those are listed in the manifest as not downloadable
and the attached files are used for that round.

  python harvest_datasets.py
"""
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import config as C

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "data" / "harvest" / "reports.json"
OUT = ROOT / "data" / "raw" / "online"
MANIFEST = OUT / "manifest.json"
EXT = (".xlsx", ".xls", ".csv")
S = requests.Session()
S.headers.update({"User-Agent": C.USER_AGENT})


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def region_of(title):
    t = title.lower()
    if re.search(r"north[- ]?(central|west)|nc/nw|ncnw", t):
        return "nwnc"
    return "ne"


def file_links(page_url):
    try:
        r = S.get(page_url, timeout=60)
    except requests.RequestException as e:
        log(f"  {e}")
        return [], False
    if r.status_code != 200:
        return [], False
    soup = BeautifulSoup(r.text, "lxml")
    links = []
    for a in soup.select("a[href]"):
        href = urljoin(page_url, a["href"])
        path = href.lower().split("?")[0]
        if path.endswith(EXT) or "/download/" in path and any(x in href.lower() for x in EXT):
            links.append(href)
    restricted = bool(re.search(r"request access", r.text, re.I)) and not links
    time.sleep(C.REQUEST_DELAY_SECONDS)
    return list(dict.fromkeys(links)), restricted


HDX_API = "https://data.humdata.org/api/3/action"
# Known IOM DTM Nigeria mobility-tracking datasets on HDX (the search below finds any new ones too)
HDX_DATASETS = [
    "nigeria-baseline-data-iom-dtm",                                  # North East baseline assessments (ward level)
    "nigeria-site-assessment-data",                                   # North East site assessments
    "nigeria-location-assessment-data",                               # North East location assessments
    "nigeria-displacement-data-north-central-west-site-assessment-iom-dtm",   # NC & NW site assessments
    "nigeria-iom-dtm-datasets",                                       # early rounds R3–R7
]
HDX_QUERIES = ["nigeria dtm baseline", "nigeria dtm site assessment", "nigeria dtm location assessment",
               "nigeria dtm master list", "nigeria displacement iom dtm", "nigeria north central west dtm"]


def hdx_packages():
    names = list(HDX_DATASETS)
    for q in HDX_QUERIES:
        try:
            r = S.get(f"{HDX_API}/package_search", params={"q": q, "fq": "groups:nga", "rows": 100}, timeout=60)
            for p in r.json().get("result", {}).get("results", []):
                org = (p.get("organization") or {}).get("name", "")
                if "international-organization-for-migration" in org or "iom" in org:
                    names.append(p["name"])
        except (requests.RequestException, ValueError) as e:
            log(f"  HDX search failed: {e}")
        time.sleep(1)
    pkgs = []
    for n in dict.fromkeys(names):
        try:
            r = S.get(f"{HDX_API}/package_show", params={"id": n}, timeout=60)
            if r.status_code == 200 and r.json().get("success"):
                pkgs.append(r.json()["result"])
        except (requests.RequestException, ValueError) as e:
            log(f"  HDX {n}: {e}")
        time.sleep(1)
    log(f"HDX: {len(pkgs)} DTM Nigeria datasets")
    return pkgs


def round_of(text):
    m = re.search(r"round[\s_-]*(\d{1,3})|\br(\d{1,3})\b|-r(\d{1,3})-|_r(\d{1,3})[_.]", text.lower())
    return int(next(g for g in m.groups() if g)) if m else None


def kind_of(text):
    t = text.lower()
    for k in ("baseline", "location", "site", "master", "return", "needs"):
        if k in t:
            return k
    return "file"


def save(entry_list, link, region, rnd, kind, title, page):
    try:
        r = S.get(link, timeout=240)
    except requests.RequestException as e:
        log(f"  download failed {link}: {e}")
        return None
    if r.status_code != 200 or len(r.content) < 500:
        return None
    ext = Path(link.split("?")[0]).suffix.lower()
    ext = ext if ext in EXT else ".xlsx"
    slug = re.sub(r"[^a-z0-9]+", "_", Path(link.split("?")[0]).stem.lower())[:40].strip("_")
    name = f"{region}_mobility_online_r{rnd:02d}_{kind}_{slug}{ext}"
    sha = hashlib.sha1(r.content).hexdigest()
    if any(e.get("sha1") == sha for e in entry_list):
        return None                               # same file already saved (e.g. xlsx and csv copies)
    (OUT / name).write_bytes(r.content)
    log(f"  saved {name}")
    time.sleep(C.REQUEST_DELAY_SECONDS)
    return {"file": name, "link": link, "sha1": sha, "round": rnd, "region": region, "kind": kind, "title": title, "page": page}


def harvest_hdx(manifest):
    saved = [f for m in manifest.values() for f in m.get("files", [])]
    for p in hdx_packages():
        page = f"https://data.humdata.org/dataset/{p['name']}"
        ptitle = p.get("title", "")
        region = region_of(ptitle + " " + p.get("notes", "")[:300])
        entry = manifest.setdefault(page, {"title": ptitle, "page": page, "region": region, "files": [], "source": "HDX"})
        have = {f["link"] for f in entry["files"]}
        res = sorted(p.get("resources", []), key=lambda x: (x.get("format", "").lower() != "xlsx"))   # prefer xlsx over csv
        seen_rounds = set()
        for rs in res:
            fmt = (rs.get("format") or "").lower()
            url = rs.get("url") or ""
            if fmt not in ("xlsx", "xls", "csv") and not url.lower().split("?")[0].endswith(EXT):
                continue
            if url in have:
                continue
            text = f"{rs.get('name', '')} {rs.get('description', '')[:300]} {url}"
            rnd = round_of(text)
            if not rnd:
                continue
            kind = kind_of(rs.get("name", "") + " " + ptitle)
            key = (rnd, kind, (rs.get("name") or "").lower().replace(".csv", "").replace(".xlsx", ""))
            if key in seen_rounds:
                continue                           # same resource published in two formats
            seen_rounds.add(key)
            reg = region_of(text + " " + ptitle)
            f = save(saved, url, reg, rnd, kind, rs.get("name", ""), page)
            if f:
                entry["files"].append(f)
                saved.append(f)
        entry["checked"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return manifest


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    manifest = harvest_hdx(manifest)
    MANIFEST.write_text(json.dumps(manifest, indent=1))
    if not REPORTS.exists():
        log("dtm.iom.int dataset pages skipped (run harvest_web.py first)")
        return
    items = json.loads(REPORTS.read_text())
    pages = [it for it in items if it.get("round") and it["component"].startswith("mt_")
             and ("/datasets/" in it["url"] or it.get("kind") == "dataset" or "humdata.org" in it["url"])]
    log(f"{len(pages)} mobility-tracking dataset pages with a round number")
    for it in pages:
        if it["url"] in manifest and manifest[it["url"]].get("files"):
            continue
        links, restricted = file_links(it["url"])
        entry = {"title": it["title"], "page": it["url"], "round": it["round"], "region": region_of(it["title"]),
                 "checked": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "files": [], "restricted": restricted}
        for k, link in enumerate(links[:4]):
            try:
                r = S.get(link, timeout=180)
            except requests.RequestException as e:
                log(f"  download failed {link}: {e}")
                continue
            if r.status_code != 200 or len(r.content) < 200:
                continue
            ext = Path(link.split("?")[0]).suffix.lower() or ".xlsx"
            slug = re.sub(r"[^a-z0-9]+", "_", it["title"].lower())[:40].strip("_")
            name = f"{entry['region']}_mobility_online_r{int(it['round']):02d}_{kind_of(it['title'])}_{slug}_{k}{ext}"
            (OUT / name).write_bytes(r.content)
            entry["files"].append({"file": name, "link": link, "sha1": hashlib.sha1(r.content).hexdigest()})
            log(f"  saved {name}")
            time.sleep(C.REQUEST_DELAY_SECONDS)
        manifest[it["url"]] = entry
    MANIFEST.write_text(json.dumps(manifest, indent=1))
    ok = sum(1 for m in manifest.values() if m["files"])
    log(f"manifest: {len(manifest)} dataset pages, {ok} with downloaded files, "
        f"{sum(1 for m in manifest.values() if m.get('restricted'))} request-access only")


if __name__ == "__main__":
    main()
