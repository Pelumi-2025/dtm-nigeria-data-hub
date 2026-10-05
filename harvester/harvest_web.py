"""
Web harvester for IOM DTM Nigeria publications.

Crawls:
  1. dtm.iom.int  – the Nigeria country listing (all pages) + the site XML sitemap
  2. response.reliefweb.int/nigeria/data – paginated data/report listing
  3. ReliefWeb API v2 (optional, needs RELIEFWEB_APPNAME) and HDX CKAN API

For every publication it:
  * classifies the component, region and states with the keyword rules in config.py
  * parses the reporting period (week, month, year) and round / report number
  * mines headline figures from the summary and (optionally) the PDF text:
      IDPs, households, returnees, IDP/returnee/camp locations (Atlas),
      new arrivals (ETT), individuals/households displaced, deaths, injuries (Flash),
      affected/displaced persons (Flood)

Results are cached incrementally in data/harvest/reports.json, so a daily run
only downloads what is new.

Publication dates, report types, regions and states of dtm.iom.int reports are taken from the DTM Nigeria
Publications Tracker (config.TRACKER_REPORTS_URL), so the Data Hub and the tracker always count the same
reports. Page URLs are stored without ?query/#fragment (dtm.iom.int also serves every page as ...?close=true).
Datasets are dated from the dtm.iom.int dataset listing.

Usage:
  python harvest_web.py                 # full incremental harvest
  python harvest_web.py --no-pdf        # skip PDF mining (fast)
  python harvest_web.py --max-pages 5   # quick test
  python harvest_web.py --sync-only     # no crawling: re-apply the Publications Tracker and de-duplicate
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dparser

import config as C

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "harvest" / "reports.json"
CACHE.parent.mkdir(parents=True, exist_ok=True)

S = requests.Session()
S.headers.update({"User-Agent": C.USER_AGENT, "Accept-Language": "en"})


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def get(url, **kw):
    for attempt in range(4):
        try:
            r = S.get(url, timeout=45, **kw)
            if r.status_code == 200:
                time.sleep(C.REQUEST_DELAY_SECONDS)
                return r
            if r.status_code in (404, 410):
                return None
            log(f"  HTTP {r.status_code} {url}")
        except requests.RequestException as e:
            log(f"  retry {attempt + 1}: {e}")
        time.sleep(3 * (attempt + 1))
    return None


# ====================================================================== parsing
MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
NUM = r"(\d{1,3}(?:[,\s]\d{3})+|\d+)"


# ---------------------------------------------------------------------- URLs and dates
DATE_VERSION = 2          # bump to make every cached item re-dated on the next run
LISTING_HINTS = {}        # canonical key -> text of the listing card (holds the publication date)
CITATION_RX = re.compile(r"International Organization for Migration \(IOM\),\s*([A-Z][a-z]{2,9}\.?\s+\d{1,2},?\s+\d{4})")
PERIOD_COVERED_RX = re.compile(r"Period Covered\s*([A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4})\s*[-\u2013\u2014]\s*([A-Z][a-z]{2,8}\.?\s+\d{1,2},?\s+\d{4})")
_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def canon(url):
    """One spelling per page: no ?query, #fragment, trailing slash or www. (dtm.iom.int also serves every
    report as ...?close=true, which used to be harvested a second time)."""
    if not url or not url.startswith("http"):
        return url
    u = urlparse(url.strip())
    return urlunparse(("https", u.netloc.lower().replace("www.", ""), u.path.rstrip("/"), "", "", ""))


def ckey(url):
    """Comparison key: canon() + decoded percent-escapes, lower case (%E2%80%94 == %e2%80%94 == \u2014)."""
    if not url or not url.startswith("http"):
        return url
    return unquote(canon(url)).lower()


def kind_of(item):
    u = item.get("url") or ""
    return "dataset" if (item.get("kind") == "dataset" or "/datasets/" in u or "humdata.org" in u) else "report"


def valid_date(iso):
    return bool(iso) and C.FIRST_VALID_DATE <= iso <= (date.today() + timedelta(days=1)).isoformat()


def parse_date(raw):
    """ISO date from 'Aug 25 2026', 'August 25, 2026', '25 August 2026' or '2026-08-25'; None if absent or implausible."""
    for part in [p.strip() for p in (raw or "").split(" | ") if p.strip()]:
        for rx, order in ((r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b", "mdy"),
                          (r"\b(\d{1,2})\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})\b", "dmy")):
            for m in re.finditer(rx, part):
                mon, day, yr = (m.group(1), m.group(2), m.group(3)) if order == "mdy" else (m.group(2), m.group(1), m.group(3))
                if mon[:3].lower() in _MON and 1 <= int(day) <= 31:
                    iso = f"{yr}-{_MON[mon[:3].lower()]:02d}-{int(day):02d}"
                    if valid_date(iso):
                        return iso
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", part)
        if m and valid_date(m.group(0)):
            return m.group(0)
    return None


def _drop_sidebars(soup):
    """Remove 'Latest datasets / Latest reports / Related' blocks: their dates belong to OTHER publications
    (this is where the old harvester picked up one site-wide date for every page)."""
    total = len(soup.get_text(" ", strip=True)) or 1
    for h in soup.find_all(["h2", "h3", "h4", "h5"]):
        if re.match(r"\s*(latest|related|recent|more from|see also)\b", h.get_text(" ", strip=True), re.I):
            box = h.find_parent(["aside", "section", "div"])
            if box is not None and len(box.get_text(" ", strip=True)) < 0.4 * total:
                box.decompose()


def page_dates(url, title, soup):
    """(published ISO date or None, basis, period_covered_start, period_covered_end)."""
    text = soup.get_text(" ", strip=True)
    cands = []
    m = CITATION_RX.search(text)
    if m:
        cands.append(("citation line", m.group(1)))
    hint = LISTING_HINTS.get(ckey(url))
    if hint:
        cands.append(("dtm.iom.int listing", hint.replace(title, " ")))
    for meta in (soup.find("meta", {"property": "article:published_time"}), soup.find("meta", {"name": "dcterms.date"}),
                 soup.find("meta", {"property": "og:published_time"})):
        if meta and meta.get("content"):
            cands.append(("page metadata", meta["content"]))
    pc = PERIOD_COVERED_RX.search(text)
    ps, pe = (parse_date(pc.group(1)), parse_date(pc.group(2))) if pc else (None, None)
    _drop_sidebars(soup)
    main = soup.find("main") or soup.find("article") or soup
    for t in main.find_all("time"):
        if t.find_parent(["header", "footer", "nav", "aside"]):
            continue
        cands.append(("page date", t.get("datetime") or t.get_text(" ", strip=True)))
        break
    for basis, raw in cands:
        d = parse_date(raw)
        if d:
            return d, basis, ps, pe
    return None, "not stated", ps, pe


def to_int(s):
    return int(re.sub(r"[^\d]", "", s)) if s else None


def parse_period(text):
    """Return (start, end) dates from strings like '(29 April - 5 May 2024)',
    '(22 - 28 April 2024)', '(25 April 2024)', '(March 2024)', '(October - December 2023)'."""
    t = text.lower().replace("–", "-").replace("—", "-")
    pats = [
        rf"(\d{{1,2}})\s*({MONTHS})\s*(\d{{4}})?\s*-\s*(\d{{1,2}})\s*({MONTHS})\s*(\d{{4}})",
        rf"(\d{{1,2}})\s*-\s*(\d{{1,2}})\s*({MONTHS})\s*(\d{{4}})",
        rf"({MONTHS})\s*(\d{{4}})?\s*-\s*({MONTHS})\s*(\d{{4}})",
        rf"(\d{{1,2}})\s*({MONTHS})\s*(\d{{4}})",
        rf"({MONTHS})\s*(\d{{4}})",
    ]
    for k, p in enumerate(pats):
        m = re.search(p, t)
        if not m:
            continue
        g = m.groups()
        try:
            if k == 0:
                end = dparser.parse(f"{g[3]} {g[4]} {g[5]}")
                start = dparser.parse(f"{g[0]} {g[1]} {g[2] or g[5]}")
            elif k == 1:
                end = dparser.parse(f"{g[1]} {g[2]} {g[3]}")
                start = dparser.parse(f"{g[0]} {g[2]} {g[3]}")
            elif k == 2:
                start = dparser.parse(f"1 {g[0]} {g[1] or g[3]}")
                end = dparser.parse(f"1 {g[2]} {g[3]}")
            elif k == 3:
                start = end = dparser.parse(f"{g[0]} {g[1]} {g[2]}")
            else:
                start = end = dparser.parse(f"1 {g[0]} {g[1]}")
            return start.date(), end.date()
        except (ValueError, OverflowError):
            continue
    return None, None


def rnd_guess(tl):
    m = re.search(r"\bround\s*(\d{1,3})\b", tl)
    return int(m.group(1)) if m else None


def classify(title, text=""):
    blob = f"{title} \n {text}".lower()
    tl = title.lower()
    comp = C.OTHER_COMPONENT
    # title first (most reliable), then body
    for scope in (tl, blob):
        for c in C.COMPONENTS:
            if any(k in scope for k in c["any"]) and not any(k in scope for k in c.get("none", [])):
                comp = c
                break
        if comp is not C.OTHER_COMPONENT:
            break
    states = [s for s in C.ALL_STATES if re.search(rf"\b{s.lower()}\b", blob)]
    # "Niger" also matches "Nigeria"/"Niger Republic" — require the word 'state' nearby or title use
    if "Niger" in states and not re.search(r"\bniger\s+(state|and|,)|,\s*niger\b|\bniger\b(?!ia)", tl):
        if not re.search(r"\bniger state\b", blob):
            states.remove("Niger")
    regions = [r for r, kws in C.REGION_KEYWORDS.items() if any(k in blob for k in kws)]
    for s in states:
        r = C.region_of_state(s)
        if r not in regions:
            regions.append(r)
    if not regions and comp["id"] in ("ett", "biometric"):
        regions = ["North East"]
    if not regions and comp["id"] in ("mt_atlas", "mt_needs") and rnd_guess(tl) and rnd_guess(tl) > 19:
        regions = ["North East"]   # NC/NW rounds only reached R19 by 2026; higher rounds are NE
    if not regions:
        regions = ["National / multi-region"]
    rnd = re.search(r"\bround\s*(\d{1,3})\b", tl)
    num = re.search(r"\b(?:report|dashboard|update)\s*(?:no\.?\s*)?(\d{1,4})\b", tl)
    return {"component": comp["id"], "component_name": comp["name"], "regions": regions,
            "states": states, "round": int(rnd.group(1)) if rnd else None,
            "number": int(num.group(1)) if num else None}


def mine_figures(component, text):
    """Pull headline numbers out of report prose. Returns dict (may be empty)."""
    t = re.sub(r"\s+", " ", text)
    f = {}

    def first(pattern, key, group=1):
        m = re.search(pattern, t, re.I)
        if m and key not in f:
            raw = m.group(group)
            # a bare 4-digit 19xx/20xx with no thousands separator is almost always a year
            if raw and raw.isdigit() and 1990 <= int(raw) <= 2100:
                return
            f[key] = to_int(raw)

    # Mobility tracking atlas / site / baseline
    first(rf"{NUM}\s*(?:internally displaced persons|IDPs)\s*(?:\(IDPs\)\s*)?(?:were\s*)?(?:identified\s*)?in\s*{NUM}\s*households", "idp_ind")
    first(rf"{NUM}\s*(?:internally displaced persons|IDPs)\s*(?:\(IDPs\)\s*)?(?:were\s*)?(?:identified\s*)?in\s*{NUM}\s*households", "idp_hh", 2)
    first(rf"identified a total of\s*{NUM}\s*(?:internally displaced persons|IDPs)", "idp_ind")
    first(rf"{NUM}\s*returnees\s*(?:were\s*(?:identified|recorded)\s*)?in\s*{NUM}\s*households", "ret_ind")
    first(rf"{NUM}\s*returnees\s*(?:were\s*(?:identified|recorded)\s*)?in\s*{NUM}\s*households", "ret_hh", 2)
    first(rf"{NUM}\s*returnees were recorded", "ret_ind")
    first(rf"assessments? (?:were|was) conducted in\s*{NUM}\s*locations", "idp_locations")
    first(rf"{NUM}\s*camps and camp-like settlements", "camps")
    first(rf"{NUM}\s*locations where (?:internally displaced persons|IDPs) (?:lived|live) among host", "hc_locations")
    first(rf"{NUM}\s*return locations", "ret_locations")
    first(rf"across\s*{NUM}\s*return locations", "ret_locations")
    # ETT
    first(rf"total of\s*{NUM}\s*new arrivals", "arrivals_ind")
    first(rf"{NUM}\s*new (?:IDP )?arrivals were recorded", "arrivals_ind")
    first(rf"{NUM}\s*internally displaced persons were recorded", "arrivals_ind")
    first(rf"total of\s*{NUM}\s*movements were recorded, including\s*{NUM}\s*arrivals", "arrivals_ind", 2)
    first(rf"including\s*{NUM}\s*arrivals and\s*{NUM}\s*departures", "departures_ind", 2)
    first(rf"{NUM}\s*were IDPs", "ett_idp_ind")
    first(rf"{NUM}\s*were returnees", "ett_ret_ind")
    first(rf"{NUM}\s*were farmers", "ett_farm_ind")
    first(rf"affected\s*{NUM}\s*individuals from\s*{NUM}\s*households", "displaced_ind")
    first(rf"affected\s*{NUM}\s*individuals from\s*{NUM}\s*households", "displaced_hh", 2)
    first(rf"total of\s*{NUM}\s*new arrivals\s*\(\s*{NUM}\s*households", "arrivals_hh", 2)
    first(rf"{NUM}\s*individuals\s*\(\s*{NUM}\s*households\s*\)\s*(?:arrived|were recorded|arrivals)", "arrivals_hh", 2)
    first(rf"{NUM}\s*households\s*\(\s*{NUM}\s*individuals\s*\)\s*(?:arrived|were recorded)", "arrivals_hh")
    first(rf"{NUM}\s*(?:individuals|persons)\s*(?:\(|in\s*){NUM}\s*households\)?\s*(?:arrived|were recorded)", "arrivals_ind")
    first(rf"total of\s*{NUM}\s*(?:movements|departures)", "departures_ind")
    # Flash / incident
    first(rf"displaced\s*{NUM}\s*(?:individuals|persons|people)(?:\s*in\s*{NUM}\s*households)?", "displaced_ind")
    m = re.search(rf"displaced\s*{NUM}\s*(?:individuals|persons|people)\s*in\s*{NUM}\s*households", t, re.I)
    if m:
        f.setdefault("displaced_hh", to_int(m.group(2)))
    first(rf"{NUM}\s*(?:individuals|persons|people)\s*(?:\(\s*{NUM}\s*households\s*\)\s*)?(?:were|have been)\s*displaced", "displaced_ind")
    first(rf"{NUM}\s*households\s*\(\s*{NUM}\s*individuals\s*\)", "displaced_hh")
    first(rf"{NUM}\s*(?:fatalities|deaths|people were killed|persons were killed|individuals were killed|killed)", "deaths")
    first(rf"{NUM}\s*(?:injuries|people were injured|persons were injured|injured)", "injured")
    if re.search(r"no (?:reports? of )?(?:injuries|fatalities|casualties)", t, re.I):
        f.setdefault("deaths", 0)
        f.setdefault("injured", 0)
    # Flood
    first(rf"{NUM}\s*(?:individuals|people|persons)\s*(?:were\s*)?affected", "affected_ind")
    # Transhumance / EWER
    first(rf"total of\s*{NUM}\s*alerts", "alerts")
    first(rf"{NUM}\s*(?:herders|pastoralists)", "herders")
    # biometric registration: "Guma LGA ... with 72,601 individuals (20,239 households)", "Logo had 13,633 individuals (3,311 households)"
    bio = []
    for m in re.finditer(r"([A-Z][A-Za-z'/-]+(?: [A-Z][A-Za-z'/-]+)?)(?: LGA)?(?: hosts? the highest number of IDPs,| has the highest number of IDPs,| had| with)?\s+(?:with\s+)?"
                         r"(\d{1,3}(?:,\d{3})+|\d+) individuals\s*(?:registered\s*)?\(?(?:from\s*)?(\d{1,3}(?:,\d{3})+|\d+) households?\)?", t):
        name = re.sub(r"\s+LGA$", "", m.group(1).strip())
        if name.lower() in ("the", "with", "total", "had", "idps", "registered") or len(name) < 3:
            continue
        if not any(b["lga"] == name for b in bio):
            bio.append({"lga": name, "ind": to_int(m.group(2)), "hh": to_int(m.group(3))})
    for m in re.finditer(r"([A-Z][A-Za-z'/-]+(?: [A-Za-z'/-]+)?) had the (?:least|lowest|most|highest)[^.]{0,40}?(\d{1,3}(?:,\d{3})+|\d+) individuals[^.]{0,30}?(\d{1,3}(?:,\d{3})+|\d+) households", t):
        name = m.group(1).strip().title()
        if not any(b["lga"].lower() == name.lower() for b in bio):
            bio.append({"lga": name, "ind": to_int(m.group(2)), "hh": to_int(m.group(3))})
    if bio:
        f["bio_lgas"] = bio[:30]
    first(rf"{NUM}\s*locations have been covered", "bio_locations")
    first(rf"{NUM}\s*individuals were identified with", "bio_vulnerable")
    # camps vs host communities, locations and wards stated in atlas / site-assessment text
    first(rf"camps? and camp-like settings\s*\(\s*{NUM}\s*individuals", "camp_ind")
    first(rf"host communities\s*\(\s*{NUM}\s*individuals", "hc_ind")
    first(rf"started in [A-Za-z]+ \d{{4}} in\s*{NUM}\s*locations", "idp_locations")
    first(rf"assessments in\s*{NUM}\s*locations", "idp_locations")
    first(rf"assessments in\s*{NUM}\s*wards", "idp_wards")
    # Transhumance Tracking Tool: early-warning alerts (events + movements) and flow monitoring (herders, animals)
    first(rf"captured\s*{NUM}\s*alerts", "alerts")
    first(rf"{NUM}\s*alerts were (?:reported|recorded)", "alerts")
    first(rf"comprising\s*{NUM}\s*events", "ttt_events")
    first(rf"comprising\s*{NUM}\s*events[^.]{{0,30}}?and\s*{NUM}\s*movements", "ttt_movements", 2)
    first(rf"{NUM}\s*\(\d+%\)\s*(?:being\s*)?event", "ttt_events")
    first(rf"{NUM}\s*\(\d+%\)\s*(?:related to\s*)?movements", "ttt_movements")
    first(rf"{NUM}\s*\(\d+%\)\s*events and\s*{NUM}", "ttt_movements", 2)
    first(rf"at\s*{NUM}\s*(?:counting|flow monitoring) points", "ttt_points")
    m2 = re.search(rf"identified\s*{NUM}\s*herders in ([A-Z][a-z]+) State and\s*{NUM}\s*herders in ([A-Z][a-z]+) State", t)
    if m2:
        st = {m2.group(2): [to_int(m2.group(1)), None], m2.group(4): [to_int(m2.group(3)), None]}
        m3 = re.search(rf"(?:animal|herd) counts? (?:was estimated at|of)\s*{NUM}\s*(?:for [A-Z][a-z]+ State\s*)?and\s*{NUM}", t)
        if m3:
            st[m2.group(2)][1], st[m2.group(4)][1] = to_int(m3.group(1)), to_int(m3.group(2))
        f["ttt_by_state"] = st
    # IDPs in host communities / camps as stated in atlases (individuals, households, %)
    first(rf"{NUM}\s*(?:individuals\s*)?(?:\(\s*\d{{1,3}}(?:\.\d)?\s*%\s*\)\s*)?(?:were\s*)?(?:living|residing|lived)\s*(?:in|among)\s*host communit", "hc_ind")
    first(rf"{NUM}\s*(?:individuals\s*)?(?:\(\s*\d{{1,3}}(?:\.\d)?\s*%\s*\)\s*)?(?:were\s*)?(?:living|residing|lived)\s*in\s*camps", "camp_ind")
    first(rf"host communities\s*\(\s*\d{{1,3}}(?:\.\d)?\s*%\s*(?:or\s*)?{NUM}", "hc_ind")
    first(rf"camps(?: and camp-like settings)?\s*\(\s*\d{{1,3}}(?:\.\d)?\s*%\s*(?:or\s*)?{NUM}", "camp_ind")
    pc = re.search(r"(\d{1,3}(?:\.\d)?)\s*(?:%|per ?cent)\s*(?:of (?:the )?IDPs\s*)?(?:were\s*|are\s*)?(?:living |residing )?in host communities", t, re.I)
    if pc:
        f["hc_pct_reported"] = float(pc.group(1))
    pc = re.search(r"(\d{1,3}(?:\.\d)?)\s*(?:%|per ?cent)\s*(?:of (?:the )?IDPs\s*)?(?:were\s*|are\s*)?(?:living |residing )?in camps", t, re.I)
    if pc:
        f["camp_pct_reported"] = float(pc.group(1))
    # sex and age (women/girls, men/boys, children under 18)
    demog = {}
    for key, pats in (("female_pct", [r"(\d{1,3}(?:\.\d)?)\s*(?:%|per ?cent)\s*(?:were\s*|are\s*)?(?:female|women and girls|girls and women)",
                                       r"(?:female|women and girls)\s*\(?\s*(\d{1,3}(?:\.\d)?)\s*%",
                                       r"(?:female|women and girls)[^.%\d]{0,30}(\d{1,3}(?:\.\d)?)\s*(?:%|per ?cent)"]),
                      ("male_pct", [r"(\d{1,3}(?:\.\d)?)\s*(?:%|per ?cent)\s*(?:were\s*|are\s*)?(?:male|men and boys|boys and men)\b",
                                     r"(?:\bmale|men and boys)\s*\(?\s*(\d{1,3}(?:\.\d)?)\s*%",
                                     r"(?:\bmales?\b|men and boys)[^.%\d]{0,30}(\d{1,3}(?:\.\d)?)\s*(?:%|per ?cent)"]),
                      ("children_pct", [r"(\d{1,3}(?:\.\d)?)\s*(?:%|per ?cent)\s*(?:of (?:the )?(?:IDPs|population|individuals)\s*)?(?:were\s*|are\s*)?children",
                                         r"children\s*(?:\(?under (?:the age of )?18(?: years)?\)?|below 18(?: years)?|aged? (?:0|under)[^)]{0,15})?\s*\(?\s*(\d{1,3}(?:\.\d)?)\s*%",
                                         r"children[^.%\d]{0,12}(?:\(?\s*under\s*18\s*\)?)?[^.%\d]{0,30}(\d{1,3}(?:\.\d)?)\s*(?:%|per ?cent)"])):
        for p in pats:
            mm = re.search(p, t, re.I)
            if mm and 0 < float(mm.group(1)) <= 100:
                demog[key] = float(mm.group(1))
                break
    if demog.get("female_pct") and not demog.get("male_pct") and demog["female_pct"] < 100:
        demog["male_pct"] = round(100 - demog["female_pct"], 1)
    if demog:
        f["demog"] = demog
    # flash reports: "including 139 children, 66 women, and 47 men"
    mm = re.search(rf"including\s*{NUM}\s*children,\s*{NUM}\s*women,?\s*and\s*{NUM}\s*men", t, re.I)
    if mm:
        f["children"], f["women"], f["men"] = to_int(mm.group(1)), to_int(mm.group(2)), to_int(mm.group(3))
    # reasons for displacement, e.g. "insurgency (92%)", "armed banditry (672,792 individuals or 45%)"
    reasons = {}
    for m in re.finditer(r"(insurgency|non-state armed group[s]? attacks?|communal (?:clash(?:es)?|violence)|armed banditry(?:/kidnapping| and kidnapping)?|banditry|kidnapping|"
                         r"farmer[s]?[- ]herder[s]? clash(?:es)?|natural disasters?|climate[- ]related disasters?|flood(?:s|ing)?)"
                         r"[^%()]{0,25}\(([^()%]{0,60}?)(\d{1,3}(?:\.\d+)?)\s*(?:%|per ?cent)\)", t, re.I):
        lab = m.group(1).lower()
        lab = ("Insurgency" if "insurg" in lab or "non-state" in lab else "Communal clashes" if "communal" in lab else
               "Armed banditry / kidnapping" if "bandit" in lab or "kidnap" in lab else "Farmer–herder clashes" if "herder" in lab
               else "Climate-related disaster")
        v = float(m.group(3))
        if 0 < v <= 100 and lab not in reasons:
            reasons[lab] = v
    if reasons:
        f["reasons_pct"] = reasons
    # drop implausible values (years, page numbers)
    return {k: v for k, v in f.items() if v is not None}


def period_fields(start, end, fallback):
    d = end or start or fallback
    if not d:
        return {}
    iso = d.isocalendar()
    return {"period_start": start.isoformat() if start else None,
            "period_end": end.isoformat() if end else None,
            "year": d.year, "month": d.strftime("%Y-%m"),
            "week": f"{iso[0]}-W{iso[1]:02d}"}


# ====================================================================== PDF text
def state_tables(pdf):
    """Read state tables (IDP / returnee households and individuals per state) from a report PDF."""
    out = {}
    try:
        for page in pdf.pages[:10]:
            for tb in page.extract_tables() or []:
                if not tb or len(tb) < 3:
                    continue
                head_rows = [r for r in tb[:3] if r and not any(c and str(c).strip().title() in C.ALL_STATES for c in r[:1])]
                header = [" ".join(str(r[j] or "") for r in head_rows).lower() for j in range(len(tb[0]))]
                whole = " ".join(header)
                for row in tb:
                    if not row or not row[0]:
                        continue
                    st = str(row[0]).strip().title().replace(" State", "")
                    if st not in C.ALL_STATES:
                        continue
                    rec = out.setdefault(st, {})
                    for j, cell in enumerate(row[1:], start=1):
                        v = to_int(str(cell or "")) if re.fullmatch(r"[\d,\s]+", str(cell or "").strip() or "x") else None
                        if v is None or j >= len(header):
                            continue
                        lab = header[j]
                        grp = "ret" if ("return" in lab or ("return" in whole and "idp" not in lab and "idp" not in whole)) else "idp"
                        unit = "hh" if re.search(r"\bhh\b|household", lab) else "ind" if re.search(r"\bind|individual|persons|population", lab) else None
                        if unit and f"{grp}_{unit}" not in rec:
                            rec[f"{grp}_{unit}"] = v
        return {k: v for k, v in out.items() if v}
    except Exception as e:
        log(f"  table read failed: {e}")
        return {}


PDF_TABLES = {}


def pdf_text(url=None, content=None, max_pages=None):
    if content is None:
        if not C.DOWNLOAD_PDFS:
            return ""
        r = get(url)
        if not r:
            return ""
        content = r.content
    if b"%PDF" not in content[:1024]:
        return ""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            PDF_TABLES["last"] = state_tables(pdf)
            return "\n".join((p.extract_text() or "") for p in pdf.pages[: (max_pages or C.PDF_MAX_PAGES)])
    except Exception as e:  # corrupted / scanned PDFs
        log(f"  pdf fail {url}: {e}")
        return ""


# ====================================================================== crawlers
def dtm_listing_urls(max_pages):
    urls = []
    for tpl in (C.DTM_COUNTRY_LISTING, C.DTM_DATASET_LISTING, *C.EXTRA_LISTINGS):
        urls += [u for u in _listing(tpl, max_pages) if u not in urls]
    return urls


def _listing(tpl, max_pages):
    urls, empty = [], 0
    for page in range(max_pages):
        r = get(tpl.format(page=page))
        if not r:
            break
        soup = BeautifulSoup(r.text, "lxml")
        found = 0
        for a in soup.select("a[href]"):
            href = urljoin(tpl.split("?")[0], a["href"])
            path = urlparse(href).path
            host = urlparse(href).netloc
            ok_dtm = "dtm.iom.int" in host and re.match(r"^/(reports|datasets|data-and-analysis|report)/", path) and \
                ("nigeria" in path.lower() or ("/datasets/" in path and "dataset_country" in tpl) or "north-central" in tpl)
            ok_rw = "reliefweb.int" in host and path.startswith("/report/nigeria/") and \
                re.search(r"displacement-tracking-matrix|dtm|iom|emergency-tracking|flash-report|biometric", path)
            if ok_dtm or ok_rw:
                href = canon(href)
                if ok_dtm:
                    card = a.find_parent(["article", "li", "tr", "div"])
                    blob = card.get_text(" ", strip=True)[:400] if card else ""
                    if blob and parse_date(blob.replace(a.get_text(" ", strip=True), " ")):
                        LISTING_HINTS.setdefault(ckey(href), blob)
                if href not in urls:
                    urls.append(href)
                    found += 1
        log(f"DTM listing {tpl.split('?')[0].rsplit('/', 1)[-1]} page {page}: {found} new")
        empty = empty + 1 if found == 0 else 0
        if empty >= 2:
            break
    return urls


def dtm_sitemap_urls():
    out, queue, seen = [], [C.DTM_SITEMAP], set()
    while queue:
        u = queue.pop()
        if u in seen:
            continue
        seen.add(u)
        r = get(u)
        if not r:
            continue
        soup = BeautifulSoup(r.content, "xml")
        for loc in soup.find_all("loc"):
            v = loc.text.strip()
            if v.endswith(".xml") or "sitemap" in v and "page=" in v:
                queue.append(v)
            elif "nigeria" in v.lower() and re.search(r"/(reports|datasets)/", v):
                v = canon(v)
                if v not in out:
                    out.append(v)
    log(f"DTM sitemap: {len(out)} Nigeria URLs")
    return out


def parse_dtm_report(url):
    r = get(url)
    if not r:
        return None
    soup = BeautifulSoup(r.text, "lxml")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else (soup.title.get_text(strip=True) if soup.title else url)
    pub, basis, pcs, pce = page_dates(url, title, soup)
    body = soup.select_one("main") or soup
    summary = " ".join(p.get_text(" ", strip=True) for p in body.select("p"))[:6000]
    pdfs = [urljoin(url, a["href"]) for a in soup.select("a[href]") if a["href"].lower().split("?")[0].endswith(".pdf")]
    return {"url": canon(url), "title": title, "published": pub, "date_basis": basis, "date_v": DATE_VERSION,
            "period_covered": [pcs, pce] if (pcs or pce) else None,
            "summary": summary, "pdfs": list(dict.fromkeys(pdfs)), "source": urlparse(url).netloc.replace("www.", ""),
            "kind": "dataset" if "/datasets/" in url else "report"}


def reliefweb_response_items(max_pages):
    items = []
    for page in range(1, max_pages + 1):
        r = get(C.RELIEFWEB_RESPONSE_LISTING.format(page=page))
        if not r:
            break
        soup = BeautifulSoup(r.text, "lxml")
        rows = soup.select("article, .views-row, tr, li.rw-river-article")
        n = 0
        for row in rows:
            a = row.find("a", href=True)
            if not a:
                continue
            txt = row.get_text(" ", strip=True)
            if not re.search(r"\b(IOM|DTM|displacement tracking)\b", txt, re.I):
                continue
            t = row.find("time")
            pub = None
            if t:
                try:
                    pub = dparser.parse(t.get("datetime") or t.get_text()).date().isoformat()
                except (ValueError, TypeError):
                    pass
            items.append({"url": urljoin("https://response.reliefweb.int", a["href"]),
                          "title": a.get_text(" ", strip=True), "published": pub, "summary": txt[:1500],
                          "pdfs": [urljoin("https://response.reliefweb.int", x["href"]) for x in row.select("a[href$='.pdf']")],
                          "source": "response.reliefweb.int"})
            n += 1
        log(f"ReliefWeb Response page {page}: {n} IOM/DTM items")
        if not rows:
            break
    return items


def reliefweb_api_items():
    app = os.environ.get("RELIEFWEB_APPNAME")
    if not app:
        log("ReliefWeb API skipped (set RELIEFWEB_APPNAME to enable)")
        return []
    items, offset = [], 0
    while True:
        body = {"limit": 1000, "offset": offset, "preset": "latest",
                "fields": {"include": ["title", "date.original", "url_alias", "file.url", "body"]},
                "filter": {"operator": "AND", "conditions": [
                    {"field": "country.iso3", "value": "nga"},
                    {"field": "source.shortname", "value": "IOM"}]}}
        try:
            r = S.post(f"{C.RELIEFWEB_API}?appname={app}", json=body, timeout=60)
            data = r.json().get("data", [])
        except (requests.RequestException, ValueError) as e:
            log(f"ReliefWeb API error: {e}")
            break
        for d in data:
            fl = d.get("fields", {})
            items.append({"url": fl.get("url_alias") or f"https://reliefweb.int/node/{d['id']}",
                          "title": fl.get("title", ""), "published": (fl.get("date", {}).get("original") or "")[:10] or None,
                          "summary": (fl.get("body") or "")[:6000],
                          "pdfs": [x["url"] for x in fl.get("file", []) if x.get("url", "").lower().endswith(".pdf")],
                          "source": "reliefweb.int"})
        if len(data) < 1000:
            break
        offset += 1000
    log(f"ReliefWeb API: {len(items)} IOM Nigeria items")
    return items


def hdx_items():
    items, start = [], 0
    while True:
        r = get(C.HDX_API, params={"q": "DTM Nigeria", "fq": "organization:international-organization-for-migration",
                                    "rows": 500, "start": start})
        if not r:
            break
        res = r.json().get("result", {})
        for p in res.get("results", []):
            if "nga" not in [g.get("name") for g in p.get("groups", [])]:
                continue
            items.append({"url": f"https://data.humdata.org/dataset/{p['name']}", "title": p.get("title", ""),
                          "published": (p.get("metadata_modified") or "")[:10] or None,
                          "summary": (p.get("notes") or "")[:6000], "pdfs": [], "source": "data.humdata.org",
                          "kind": "dataset", "date_basis": "last updated on HDX"})
        start += 500
        if start >= res.get("count", 0):
            break
    log(f"HDX: {len(items)} DTM Nigeria datasets")
    return items


# ====================================================================== main
# ---------------------------------------------------------------- LGA gazetteer
def load_gazetteer():
    """LGA names per state, taken from the mobility and ETT datasets (build_baseline.py output)."""
    gz = {}
    f = ROOT / "data" / "baseline.json"
    if not f.exists():
        return gz
    b = json.loads(f.read_text())
    for key in ("north_east", "nc_nw"):
        for row in b[key]["lga_rounds"]["rows"]:
            gz.setdefault(row[1], set()).add(row[2])
    for row in b["ett"]["lga_year"]["rows"]:
        gz.setdefault(row[1], set()).add(row[2])
    return {s: sorted(v, key=len, reverse=True) for s, v in gz.items()}


GAZ = None


def find_lgas(text, states):
    global GAZ
    if GAZ is None:
        GAZ = load_gazetteer()
    found = []
    t = re.sub(r"\s+", " ", text)
    for s in states or GAZ.keys():
        for lga in GAZ.get(s, []):
            if lga.lower() in ("unknown", "nan") or lga in C.TRACKED_STATES:
                continue
            pat = re.escape(lga).replace("/", r"\s*/\s*")
            if re.search(rf"\b{pat}\b", t, re.I) and lga not in found:
                found.append(lga)
    return found[:25]


def count_communities(text):
    t = re.sub(r"\s+", " ", text)
    names = set()
    for m in re.finditer(r"(?:communit(?:y|ies)|villages?|settlements?) of ([A-Z][^.;:()]{2,400}?)(?=\s(?:of|in)\s[A-Z][\w' /-]*\s(?:Local|LGA)|,? all in|\.\s|;|$)", t):
        for part in re.split(r",\s*|\s+and\s+", m.group(1)):
            part = re.split(r"\s+(?:in|of)\s+", part.strip())[0].strip()
            if part and part[0].isupper() and len(part) < 40:
                names.add(part.lower())
    return len(names) or None


def enrich(item, use_pdf):
    text = item.get("summary", "")
    if item.get("pdf_text"):
        text = text + "\n" + item.pop("pdf_text")
    elif use_pdf and item.get("pdfs"):
        text = text + "\n" + pdf_text(item["pdfs"][0])
    cls = classify(item["title"], text)
    start, end = parse_period(item["title"])
    if item.get("published") and not valid_date(item["published"][:10]):
        item["published"] = None
    pub = date.fromisoformat(item["published"][:10]) if item.get("published") else None
    item.setdefault("date_basis", "source listing" if pub else "not stated")
    item.setdefault("date_v", DATE_VERSION)
    if not start and not end and item.get("period_covered"):
        pcs, pce = item["period_covered"]
        start, end = (date.fromisoformat(pcs) if pcs else None), (date.fromisoformat(pce) if pce else None)
    item.update(cls)
    item.update(period_fields(start, end, pub))
    PDF_TABLES["last"] = PDF_TABLES.get("last") or {}
    item["figures"] = mine_figures(cls["component"], text)
    if cls["component"].startswith("mt_") and PDF_TABLES.get("last"):
        item["figures"]["state_table"] = PDF_TABLES["last"]
    PDF_TABLES["last"] = {}
    if cls["component"] in ("flash", "ett", "flood", "mt_needs"):
        item["lgas"] = find_lgas(text[:20000], cls["states"])
        n = count_communities(text[:20000])
        if n:
            item["figures"]["communities"] = n
    item["text_hash"] = hashlib.md5(text.encode()).hexdigest()
    item["harvested"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    item.pop("summary", None)
    item["snippet"] = text[:400]
    return item


def local_reports(folder=None, max_pages=None):
    """PDF reports in data/reports/ or in any folder on your computer (for example the DTM Nigeria IM
    SharePoint 'Reports and Publications' library synced with OneDrive). Every sub-folder is read."""
    folder = Path(folder) if folder else ROOT / "data" / "reports"
    items = []
    files = sorted(folder.rglob("*.pdf")) if folder.exists() else []
    for k, f in enumerate(files, 1):
        content = f.read_bytes()
        text = pdf_text(content=content, max_pages=max_pages)
        if k % 25 == 0:
            log(f"  read {k}/{len(files)} PDFs")
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 12]
        title = f.stem.replace("_", " ").replace("-", " ")
        pub = None
        m = re.search(rf"(\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}}|(?:{MONTHS})\s+\d{{4}})", title.lower() + " " + text[:3000].lower())
        if m:
            try:
                pub = dparser.parse(m.group(1), default=datetime(2000, 1, 1)).date().isoformat()
            except (ValueError, OverflowError):
                pass
        items.append({"url": f"local:{f.relative_to(folder).as_posix()}", "title": title if len(title) > 15 or not lines else lines[0][:160],
                      "published": pub, "summary": "", "pdf_text": text, "pdfs": [], "source": "uploaded report",
                      "hash": hashlib.md5(content).hexdigest()})
    log(f"local reports: {len(items)} PDFs in {folder}")
    return items


def dedupe_key(item):
    """A report and a dataset may share a title (e.g. 'North-East Displacement Report Round 51'); they are
    different publications, so the kind is part of the key."""
    t = re.sub(r"[^a-z0-9]+", " ", (item.get("title") or "").lower()).strip()
    return kind_of(item), t


# ---------------------------------------------------------------- Publications Tracker (reference list)
def load_json_source(src, label):
    try:
        if src.startswith("http"):
            r = S.get(src, timeout=60)
            r.raise_for_status()
            return r.json()
        return json.loads(Path(src).read_text(encoding="utf-8"))
    except (requests.RequestException, ValueError, OSError) as e:
        log(f"{label} not available ({e}); keeping the Data Hub's own classification")
        return None


def load_tracker():
    data = load_json_source(os.environ.get("TRACKER_REPORTS") or C.TRACKER_REPORTS_URL, "Publications Tracker")
    if data and data.get("reports"):
        log(f"Publications Tracker: {len(data['reports'])} reports (generated {data.get('generated_at')})")
        return data
    return None


def load_taxonomy():
    tax = load_json_source(os.environ.get("TRACKER_TAXONOMY") or C.TRACKER_TAXONOMY_URL, "Tracker taxonomy")
    return tax if tax and tax.get("components") else None


def _norm(text):
    text = (text or "").lower()
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = re.sub(r"[^\w&/\-\s]", " ", text)
    return " " + re.sub(r"\s+", " ", text).strip() + " "


def tracker_category(title, summary, tax):
    """Same keyword rules as the Publications Tracker (config/taxonomy.json there): title first, then summary."""
    if not tax:
        return None
    for text in (_norm(title), _norm(summary)):
        for rule in tax["components"]:
            if rule.get("require_state") and not any(re.search(rf"\b{s.lower()}\b", title.lower()) for s in C.ALL_STATES):
                continue
            if any(_norm(x).strip() in text for x in rule.get("exclude", []) if x.strip()):
                continue
            for term in rule["include"]:
                t = term.lower()
                if (t in text) if (t.startswith(" ") or t.endswith(" ")) else (_norm(t).strip() in text):
                    return rule["key"]
    return tax.get("unclassified_key", "other")


def refresh_period(it):
    s_, e_ = it.get("period_start"), it.get("period_end")
    if not s_ and not e_ and it.get("period_covered"):
        s_, e_ = it["period_covered"]
    pub = it.get("published")
    it.update(period_fields(date.fromisoformat(s_) if s_ else None, date.fromisoformat(e_) if e_ else None,
                            date.fromisoformat(pub[:10]) if pub else None))


def pub_status(it, tracker, tax):
    """Whether a page counts as a DTM Nigeria publication on the Publications page (same rule as the tracker)."""
    if kind_of(it) == "dataset":
        return "dataset"
    if it.get("in_tracker"):
        return "counted"
    if "dtm.iom.int" not in (it.get("url") or ""):
        return "other source"
    if it.get("tracker_dup"):
        return "second page of a counted report"
    rx = (tax or {}).get("scope_title_regex", r"^\s*(dtm\s+)?nig[eé]ria\b")
    if rx and not re.search(rx, it.get("title", ""), re.I):
        return "outside DTM Nigeria scope"
    if not tracker or (it.get("harvested") or "") > (tracker.get("generated_at") or ""):
        return "counted (new, not yet in the tracker)"
    return "not counted by the tracker"


def apply_tracker(cache, tracker, tax):
    """Give every dtm.iom.int report the tracker's publication date, report type, region and states, and
    correct the Data Hub product where the old keyword rules filed it wrongly."""
    names = {c["id"]: c["name"] for c in C.COMPONENTS}
    names["other"] = C.OTHER_COMPONENT["name"]
    rows = {ckey(r["url"]): r for r in tracker["reports"]} if tracker else {}
    titles = {dedupe_key({"title": r["title"], "url": r["url"]}) for r in rows.values()}
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    # reports the tracker lists but the crawl could not open: count them from the tracker's record
    for k, r in rows.items():
        if k not in cache:
            comp = C.TRACK2HUB.get(r["component_key"]) or "other"
            cache[k] = {"url": canon(r["url"]), "title": r["title"], "published": r.get("date"), "source": "dtm.iom.int",
                        "kind": "report", "pdfs": [r["pdf"]] if r.get("pdf") else [], "figures": {}, "component": comp,
                        "component_name": names.get(comp, comp), "regions": C.pub_regions(r.get("regions"), r.get("states")) or ["National / multi-region"],
                        "states": r.get("states") or [], "round": r.get("round"), "number": None, "harvested": now,
                        "from_tracker": True, "needs_fetch": True, "snippet": (r.get("summary") or "")[:400]}
    moved = 0
    for k, it in cache.items():
        if not tracker and it.get("pub_status"):
            continue    # tracker unreachable this run: keep what the last successful sync decided
        src_dtm = "dtm.iom.int" in (it.get("url") or "")
        r = rows.get(k)
        if r:
            it["in_tracker"] = True
            it["published"] = r.get("date") or it.get("published")
            it["date_basis"] = "Publications Tracker (" + (r.get("date_basis") or "publication date") + ")"
            it["date_v"] = DATE_VERSION
            it["pub_cat"] = r["component_key"]
            it["pub_regions"] = C.pub_regions(r.get("regions"), r.get("states"))
            it["pub_states"] = r.get("states") or []
            if r.get("pdf") and not it.get("pdfs"):
                it["pdfs"] = [r["pdf"]]
            hub = C.TRACK2HUB.get(r["component_key"])
            if hub and hub != it.get("component"):
                it.setdefault("component_was", it.get("component"))
                it["component"], it["component_name"] = hub, names.get(hub, hub)
                moved += 1
        else:
            it["in_tracker"] = False
            if src_dtm and it.get("date_v") != DATE_VERSION:
                # dated by the old harvester (one site-wide date for every page): unknown until re-dated
                it["published"], it["date_basis"] = None, "pending re-check on dtm.iom.int"
            # same title as a tracker report = a second page for it (the tracker counts it once)
            it["tracker_dup"] = bool(tracker) and src_dtm and kind_of(it) == "report" and dedupe_key(it) in titles
            it["pub_cat"] = tracker_category(it.get("title", ""), it.get("snippet", ""), tax) or C.HUB2TRACK.get(it.get("component"), "other")
            it["pub_regions"] = C.pub_regions([x for x in it.get("regions", []) if x != "National / multi-region"], it.get("states"))
            it["pub_states"] = it.get("states") or []
        it["pub_status"] = pub_status(it, tracker, tax)
        if it.get("published") and not valid_date(it["published"][:10]):
            it["published"] = None
        refresh_period(it)
    log(f"tracker sync: {sum(1 for x in cache.values() if x.get('in_tracker'))} reports matched, "
        f"{moved} moved to the correct product, {sum(1 for x in cache.values() if x.get('tracker_dup'))} second pages")


def redate(cache, max_listing_pages):
    """Items dated by the old harvester (one site-wide date for every page) get their real date:
    the listing card on dtm.iom.int, else the page itself. Tracker reports are already fixed."""
    todo = [k for k, it in cache.items() if it.get("date_v") != DATE_VERSION and "dtm.iom.int" in (it.get("url") or "")]
    if not todo:
        return
    log(f"re-dating {len(todo)} items harvested before the date fix")
    if any("/datasets/" in cache[k]["url"] for k in todo):
        _listing(C.DTM_DATASET_LISTING, max(max_listing_pages, 40))   # dataset dates live on the listing cards
    for n, k in enumerate(todo, 1):
        it = cache[k]
        r = get(it["url"])
        if r:
            soup = BeautifulSoup(r.text, "lxml")
            pub, basis, pcs, pce = page_dates(it["url"], it.get("title", ""), soup)
        else:
            hint = LISTING_HINTS.get(k)
            pub, basis, pcs, pce = parse_date(hint), "dtm.iom.int listing", None, None
        it["published"], it["date_basis"], it["date_v"] = pub, basis if pub else "not stated", DATE_VERSION
        if pcs or pce:
            it["period_covered"] = [pcs, pce]
        refresh_period(it)
        if n % 50 == 0:
            log(f"  re-dated {n}/{len(todo)}")


def load_cache(refresh):
    """Cache keyed by ckey(); old '?close=true' copies are folded into the clean URL."""
    cache = {}
    if refresh or not CACHE.exists():
        return cache
    items = json.loads(CACHE.read_text())
    items.sort(key=lambda x: ("?" in (x.get("url") or ""), x.get("duplicate_of") is not None))
    folded = 0
    for x in items:
        if (x.get("url") or "").startswith("http") and "#" not in x["url"]:
            x["url"] = canon(x["url"])
        k = ckey(x.get("url"))
        if k in cache:
            folded += 1
            continue
        cache[k] = x
    log(f"cache: {len(cache)} items ({folded} duplicate URL spellings folded)")
    return cache


def mark_duplicates(cache):
    """Same kind + same title = one publication. dtm.iom.int wins (tracker-listed first); figures read from an
    uploaded PDF or another source are copied onto the dtm.iom.int record so nothing is lost."""
    rank = lambda x: (not x.get("in_tracker"), x.get("source") != "dtm.iom.int", x.get("source") != "uploaded report",  # noqa: E731
                      x.get("published") or "9999")
    seen = {}
    for it in sorted(cache.values(), key=rank):
        k = dedupe_key(it)
        first = seen.get(k)
        it["duplicate_of"] = first["url"] if first else None
        if first is None:
            seen[k] = it
        else:
            for fk, fv in (it.get("figures") or {}).items():
                first.setdefault("figures", {}).setdefault(fk, fv)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=C.MAX_LISTING_PAGES)
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-process everything, ignoring the cache")
    ap.add_argument("--local-folder", help="also read every PDF in this folder (e.g. the synced SharePoint 'Reports and Publications' library)")
    ap.add_argument("--local-only", action="store_true", help="read local PDFs only, skip the websites")
    ap.add_argument("--sync-only", action="store_true", help="no crawling: only fold duplicate URLs, apply the Publications Tracker and de-duplicate")
    a = ap.parse_args()

    cache = load_cache(a.refresh)
    save = lambda: CACHE.write_text(json.dumps(sorted(cache.values(), key=lambda x: x.get("published") or ""), indent=1))  # noqa: E731

    if a.local_folder:
        n_new = 0
        for it in local_reports(a.local_folder, max_pages=30):
            it["url"] = "local:sharepoint/" + it["url"].split(":", 1)[1]
            it["source"] = "IM SharePoint library"
            old = cache.get(it["url"])
            if not old or old.get("hash") != it["hash"]:
                cache[it["url"]] = enrich(it, False)
                n_new += 1
        log(f"local folder: {n_new} new or changed reports read")
        save()
        if a.local_only:
            log(f"saved {len(cache)} items -> {CACHE}")
            return

    tracker, tax = load_tracker(), load_taxonomy()
    if tax:
        (CACHE.parent / "tracker_taxonomy.json").write_text(json.dumps(tax, indent=1))
    if tracker:
        (CACHE.parent / "tracker_meta.json").write_text(json.dumps(
            {"generated_at": tracker.get("generated_at"), "count": len(tracker["reports"]),
             "components": tracker.get("components") or []}, indent=1))

    if not a.sync_only:
        # 1. DTM website (+ every report the tracker knows, so nothing it counts is missing here)
        urls = dtm_listing_urls(a.max_pages)
        urls += [u for u in dtm_sitemap_urls() if u not in urls]
        if tracker:
            urls += [canon(r["url"]) for r in tracker["reports"] if canon(r["url"]) not in urls]
        urls += [x["url"] for x in cache.values() if x.get("needs_fetch") and x["url"] not in urls]
        new = [u for u in urls if ckey(u) not in cache or cache[ckey(u)].get("needs_fetch")]
        log(f"DTM: {len(urls)} URLs, {len(new)} new")
        for k, u in enumerate(new, 1):
            it = parse_dtm_report(u)
            if it:
                cache[ckey(u)] = enrich(it, not a.no_pdf)
                log(f"  [{k}/{len(new)}] {cache[ckey(u)]['component']:<12} {it['title'][:80]}")
            if k % 25 == 0:
                save()

        # 2. PDFs you uploaded to data/reports/ (re-read when the file changes)
        for it in local_reports():
            old = cache.get(it["url"])
            if not old or old.get("hash") != it["hash"]:
                cache[it["url"]] = enrich(it, False)

        # 3. ReliefWeb Response, ReliefWeb API, HDX
        for it in reliefweb_response_items(a.max_pages) + reliefweb_api_items() + hdx_items():
            k = ckey(it["url"])
            if k not in cache:
                cache[k] = enrich(it, not a.no_pdf)

        # 4. real publication dates for items harvested before the date fix
        redate(cache, a.max_pages)

    apply_tracker(cache, tracker, tax)
    mark_duplicates(cache)
    save()
    log(f"saved {len(cache)} items -> {CACHE}")


if __name__ == "__main__":
    main()
