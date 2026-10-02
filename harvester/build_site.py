"""
Merges everything into the dashboard and builds dist/:

  data/baseline.json             Excel files in data/raw/            (build_baseline.py)
  data/harvest/reports.json      dtm.iom.int, ReliefWeb, HDX, PDFs   (harvest_web.py)
  data/harvest/dtm_api.json      DTM public API                      (dtm_api.py, optional)

Output:
  dist/index.html   dashboard with ALL data embedded -> works fully offline
  dist/data.json    same data; the live site re-reads it to pick up new harvests
  dist/sw.js, manifest.webmanifest, .nojekyll
"""
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "baseline.json"
HARV = ROOT / "data" / "harvest" / "reports.json"
API = ROOT / "data" / "harvest" / "dtm_api.json"
SITE = ROOT / "site"
DIST = ROOT / "dist"
NE, NC = "North East", "North Central & North West"
FILL_KEYS = ["idp_ind", "idp_hh", "ret_ind", "ret_hh", "idp_locations", "camps", "hc_locations", "ret_locations", "camp_ind", "hc_ind"]
LOC_KEYS = ["idp_locations", "camps", "hc_locations", "ret_locations", "camp_ind", "hc_ind"]


def load_harvest():
    if not HARV.exists():
        return []
    import config as C
    items = [x for x in json.loads(HARV.read_text()) if not x.get("duplicate_of") and x["component"] not in C.EXCLUDED_COMPONENTS]
    for it in items:
        if it.get("regions") == ["National / multi-region"] and it["component"].startswith("mt_") \
                and (it.get("year") or 9999) < 2019:
            it["regions"] = ["North East"]
    return items


def op_region(it):
    r = it.get("regions", [])
    if NC in r and NE not in r:
        return NC
    if NE in r:
        return NE
    return None


def link_rounds(region_block, items, region):
    """Attach every harvested report/dataset of a round to it, fill missing figures, keep report figures for cross-checking."""
    rounds = {r["round"]: r for r in region_block["rounds"]}
    for it in items:
        if not it.get("round") or not it["component"].startswith("mt_") or op_region(it) != region:
            continue
        r = rounds.get(it["round"])
        if r is None:
            r = rounds[it["round"]] = {"round": it["round"], "date": it.get("month"), "year": it.get("year"), "source": "report"}
        kind = "dataset" if (it.get("kind") == "dataset" or "/datasets/" in it["url"] or "humdata" in it["url"]) else "report"
        links = r.setdefault("links", [])
        if it["url"] not in [l if isinstance(l, str) else l["u"] for l in links]:
            links.append({"u": it["url"], "t": it["title"], "k": kind})
        f = it.get("figures", {})
        if f.get("state_table"):
            r.setdefault("states_reported", {}).update(f["state_table"])
        rep = r.setdefault("reported", {})
        for k in FILL_KEYS + ["idp_wards"]:
            if f.get(k) is not None and rep.get(k) is None:
                rep[k] = f[k]
        if f.get("demog") and not r.get("demog"):
            r["demog"] = {**f["demog"], "src": it["url"]}
        for k in ("hc_pct_reported", "camp_pct_reported"):
            if f.get(k) is not None:
                rep.setdefault(k, f[k])
        if f.get("reasons_pct") and not r.get("reasons_pct"):
            r["reasons_pct"] = f["reasons_pct"]
        keys = FILL_KEYS if r.get("source") != "dataset" else LOC_KEYS
        for k in keys:
            if r.get(k) is None and f.get(k) is not None:
                r[k] = f[k]
        if not r.get("date") and it.get("month"):
            r["date"], r["year"] = it["month"], it.get("year")
    # old-style string links -> objects
    for r in rounds.values():
        r["links"] = [({"u": l, "t": "Published report", "k": "report"} if isinstance(l, str) else l) for l in r.get("links", [])]
    region_block["rounds"] = sorted(rounds.values(), key=lambda z: z["round"])


def apply_api(base):
    if not API.exists():
        return
    api = json.loads(API.read_text())
    for region, key in ((NE, "north_east"), (NC, "nc_nw")):
        tot = {}
        for row in api.get("admin1", []):
            if row["region"] != region or row.get("round") in (None, "") or row.get("idp_ind") is None:
                continue
            rn = int(row["round"])
            t = tot.setdefault(rn, {"idp_ind": 0, "date": row.get("date"), "reasons": {}})
            t["idp_ind"] += int(row["idp_ind"])
            if row.get("reason"):
                t["reasons"][row["reason"]] = t["reasons"].get(row["reason"], 0) + int(row["idp_ind"])
        rounds = {r["round"]: r for r in base[key]["rounds"]}
        for rn, t in tot.items():
            r = rounds.setdefault(rn, {"round": rn, "source": "api", "date": (t["date"] or "")[:7] or None})
            r["api_idp_ind"] = t["idp_ind"]
            if r.get("idp_ind") is None:
                r["idp_ind"] = t["idp_ind"]
            if t["reasons"] and not r.get("reasons_pct"):
                s = sum(t["reasons"].values()) or 1
                r["reasons_pct"] = {k: round(v / s * 100, 1) for k, v in t["reasons"].items()}
            if not r.get("year") and r.get("date"):
                r["year"] = int(r["date"][:4])
        base[key]["rounds"] = sorted(rounds.values(), key=lambda z: z["round"])


HEAD_KEYS = ["idp_hh", "idp_ind", "ret_hh", "ret_ind", "camp_hh", "camp_ind", "hc_hh", "hc_ind", "int_ind", "rel_ind"]


def report_first(base):
    """Headline numbers come from the published reports. Data-file values are kept as ds_* and only
    shown where a report figure is not available; fig_src records where every headline value comes from."""
    for key in ("north_east", "nc_nw"):
        for r in base[key]["rounds"]:
            rep = r.get("reported") or {}
            src = {}
            for k in HEAD_KEYS:
                ds = r.get(k) if r.get("source") == "dataset" and k not in (r.get("from_report") or []) else None
                if ds is not None:
                    r["ds_" + k] = ds
                if rep.get(k) is not None:
                    r[k] = rep[k]
                    src[k] = "report"
                elif r.get(k) is not None:
                    src[k] = "report" if (r.get("source") != "dataset" or k in (r.get("from_report") or [])) else "data file"
            # state-level figures read from report tables by the harvester
            r["fig_src"] = src


def pct100(vals):
    """Whole-number percentages that always add up to exactly 100 (largest remainder)."""
    tot = sum(v for v in vals if v)
    if not tot:
        return [None for _ in vals]
    raw = [(v or 0) / tot * 100 for v in vals]
    fl = [int(x) for x in raw]
    left = 100 - sum(fl)
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - fl[i], reverse=True)[:left]:
        fl[i] += 1
    return [fl[i] if vals[i] else (0 if vals[i] == 0 else None) for i in range(len(vals))]


CATS = [("hc", "IDPs in host communities"), ("camp", "IDPs in camps and camp-like settings"),
        ("int", "IDPs in integrated sites"), ("rel", "IDPs in relocated / resettled sites"),
        ("oth", "Other (in the report total, not broken down in the report)")]


def idp_split(base):
    """Where IDPs live, per round: report numbers when the report states the split, otherwise the
    round's data file — never mixed — with percentages that add up to exactly 100."""
    for key in ("north_east", "nc_nw"):
        for r in base[key]["rounds"]:
            rep = r.get("reported") or {}
            if rep.get("hc_ind") is not None and rep.get("camp_ind") is not None:
                vals = {"hc": rep["hc_ind"], "camp": rep["camp_ind"], "int": rep.get("int_ind"), "rel": rep.get("rel_ind")}
                total = rep.get("idp_ind") or r.get("idp_ind")
                listed = sum(v for v in vals.values() if v)
                vals["oth"] = (total - listed) if total and total > listed else None
                total = max(total or 0, listed)
                src = "report"
            elif r.get("ds_hc_ind") is not None or r.get("ds_camp_ind") is not None or (r.get("source") == "dataset" and r.get("camp_ind") is not None):
                vals = {"hc": r.get("ds_hc_ind", r.get("hc_ind")), "camp": r.get("ds_camp_ind", r.get("camp_ind")), "int": r.get("int_ind"), "rel": r.get("rel_ind"), "oth": None}
                total = sum(v for v in vals.values() if v)
                src = "data file"
            else:
                continue
            ks = [k for k, _ in CATS]
            pc = pct100([vals.get(k) for k in ks])
            r["split"] = {"src": src, "total": total, "ind": {k: vals.get(k) for k in ks}, "pct": dict(zip(ks, pc)),
                          "check": sum(p for p in pc if p)}


def crossref(base):
    """Dataset (Excel) vs figures stated in the published report vs DTM API, per round."""
    rows = []
    for region, key in ((NE, "north_east"), (NC, "nc_nw")):
        for r in base[key]["rounds"]:
            rep = r.get("reported", {})
            for m in ("idp_hh", "idp_ind", "ret_hh", "ret_ind", "camp_ind", "hc_ind", "idp_lgas", "idp_wards", "idp_locations"):
                if m in ("camp_ind", "hc_ind") and r.get("source") == "dataset" and m in (r.get("from_report") or []):
                    continue          # the region's data file has no camp split; the figure shown IS the report figure
                if m == "idp_locations":
                    ds = r.get("idp_sites") if r.get("source") == "dataset" else None
                else:
                    ds = r.get(m) if r.get("source") == "dataset" else None
                rp = rep.get(m) if rep else (r.get(m) if r.get("source") == "report" and m in ("idp_hh", "idp_ind", "ret_hh", "ret_ind") else None)
                ap = r.get("api_idp_ind") if m == "idp_ind" else None
                if ds is None and rp is None and ap is None:
                    continue
                vals = [v for v in (ds, rp, ap) if v is not None]
                spread = (max(vals) - min(vals)) / max(vals) * 100 if len(vals) > 1 and max(vals) else None
                link = next((l["u"] for l in r.get("links", []) if l.get("k") == "report"), None) or \
                       next((l["u"] for l in r.get("links", [])), None) or "https://dtm.iom.int/nigeria"
                rows.append({"region": region, "round": r["round"], "date": r.get("date"), "measure": m,
                             "dataset": ds, "report": rp, "api": ap,
                             "diff_pct": None if spread is None else round(spread, 2),
                             "status": "Only one source" if len(vals) == 1 else ("Matches" if spread <= 0.5 else "Differs"),
                             "link": link, "against": r.get("reported_src") or ("published report" if rp is not None else None)})
    return rows


def ett_crosscheck(base_ett, items):
    daily = base_ett.get("daily", [])
    rows = []
    for it in items:
        if it["component"] != "ett" or not it.get("figures", {}).get("arrivals_ind") or not it.get("period_start"):
            continue
        s, e = it["period_start"], it.get("period_end") or it["period_start"]
        states = set(it.get("states") or [])
        excel = sum(x["i"] for x in daily if s <= x["d"] <= e and (not states or x["s"] in states))
        rows.append({"report": it.get("number"), "title": it["title"], "url": it["url"], "start": s, "end": e,
                     "reported": it["figures"]["arrivals_ind"], "excel": excel, "states": it.get("states", [])})
    return sorted(rows, key=lambda r: r["start"])


ETT_SERIES = "https://dtm.iom.int/product-series/emergency-tracking-report"
SEED_ETT = [  # number, from, to, states, arrivals, IDPs, returnees, farmers, url
    (25, "2017-07-25", "2017-08-01", ["Adamawa", "Borno"], None, None, None, None, "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-report-no-25-25"),
    (204, "2020-12-28", "2021-01-01", ["Adamawa", "Borno"], None, None, None, None, "https://reliefweb.int/sites/reliefweb.int/files/resources/IOM%20Nigeria%20DTM%20Emergency%20Tracking%20Tool%20(ETT)%20Report%20No.204%20%20(28%20December%202020%20-%201%20January%202021).pdf"),
    (321, "2023-03-27", "2023-04-02", ["Adamawa", "Borno"], 1592, None, None, None, "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-report-no-321-27-march-2-april-2023"),
    (328, "2023-05-15", "2023-05-21", ["Adamawa", "Borno"], 2414, None, None, None, "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-report-no-328-15-21-may-2023"),
    (382, "2024-05-27", "2024-06-02", ["Adamawa", "Borno"], 1259, None, None, None, "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-report-no-382-27-may-2-june-2024"),
    (17, "2024-10-28", "2024-11-03", ["Benue"], None, None, None, None, "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-report-no-17-benue-state-28-october-03-november-2024"),
    (416, "2025-01-20", "2025-01-26", ["Adamawa", "Borno"], None, None, None, None, "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-summary-movements-borno-and-adamawa-states-dashboard-416-20-26-january-2025"),
    (326, "2023-05-01", "2023-05-07", ["Adamawa", "Borno"], 1639, None, None, None, "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-report-no-326-1-7-may-2023"),
    (378, "2024-04-29", "2024-05-05", ["Adamawa", "Borno"], 1005, None, None, None, ETT_SERIES),
    (404, "2024-10-28", "2024-11-03", ["Adamawa", "Borno"], 826, None, None, None, "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-summary-movements-borno-and-adamawa-states-dashboard-404-28-october-3-november-2024"),
    (411, "2024-12-16", "2024-12-22", ["Adamawa", "Borno"], None, None, None, None, "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-summary-movements-borno-and-adamawa-states-dashboard-411-16-22-december-2024"),
    (425, "2025-03-24", "2025-03-30", ["Adamawa", "Borno"], 891, None, None, None, "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-425-24-30-march-2025"),
    (None, "2025-05-19", "2025-05-25", ["Benue"], 116, None, None, None, "https://dtm.iom.int/operations/north-central-and-north-west"),
    (None, "2025-10-20", "2025-10-26", ["Borno"], 253, 100, 142, 11, "https://dtm.iom.int/nigeria?page=3"),
    (None, "2025-10-20", "2025-10-26", ["Adamawa"], 965, None, None, None, "https://dtm.iom.int/nigeria?page=3"),
    (None, "2025-10-27", "2025-11-02", ["Borno"], 255, 113, 129, 13, "https://dtm.iom.int/nigeria?page=3"),
    (None, "2025-11-03", "2025-11-09", ["Adamawa"], 1032, None, None, None, "https://dtm.iom.int/nigeria?page=3"),
    (None, "2025-12-15", "2025-12-21", ["Adamawa"], 1249, None, None, None, "https://dtm.iom.int/nigeria?page=2"),
    (518, "2026-01-05", "2026-01-11", ["Borno"], 340, 211, 129, None, "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-518-borno-state-5-11-january-2026"),
    (None, "2026-01-05", "2026-01-11", ["Adamawa"], 674, None, None, None, "https://dtm.iom.int/nigeria?page=2"),
    (None, "2026-01-12", "2026-01-18", ["Borno"], 268, 212, 56, None, ETT_SERIES),
    (None, "2026-01-12", "2026-01-18", ["Adamawa"], 901, None, None, None, ETT_SERIES),
    (None, "2026-01-12", "2026-01-18", ["Yobe"], 48, 48, None, None, ETT_SERIES),
    (None, "2026-01-12", "2026-01-18", ["Benue"], 93, 93, None, None, "https://dtm.iom.int/nigeria?page=2"),
    (523, "2026-01-19", "2026-01-25", ["Borno"], 551, 181, 370, None, "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-523-borno-state-19-25-january-2026"),
]
SEED_ETT_EXTRA = [  # early ETT reports (arrivals and departures were both reported then)
    {"n": 60, "s": "2018-03-27", "e": "2018-04-02", "st": ["Borno", "Adamawa", "Yobe"], "arr": None, "dep": None,
     "lga_arrivals": {"Bama": 3087, "Gwoza": 859, "Madagali": 73, "Mobbar": 303},
     "url": "https://dtm.iom.int/sites/g/files/tmzbdl1461/files/reports/IOM%20Nigeria%20DTM%20Emergency%20Tracking%20Tool%20(ETT)%20-%20Report%20No.60.pdf"},
    {"n": 68, "s": "2018-05-23", "e": "2018-05-29", "st": ["Adamawa", "Borno"], "arr": 3956, "dep": 501,
     "url": "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-report-no-68-23"},
    {"n": 322, "s": "2023-04-03", "e": "2023-04-09", "st": ["Adamawa", "Borno"], "arr": 959, "dep": None,
     "url": "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-322-3-9-april-2023"},
    {"n": 343, "s": "2023-08-28", "e": "2023-09-03", "st": ["Adamawa", "Borno", "Yobe"], "arr": 3763, "dep": None,
     "url": "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-343-28-august-3-september-2023"},
    {"n": 284, "s": "2022-07-11", "e": "2022-07-17", "st": ["Adamawa", "Borno"], "arr": 3975, "dep": None,
     "url": "https://displacement.iom.int/reports/nigeria-emergency-tracking-tool-report-284-11-17-july-2022"},
    {"n": 286, "s": "2022-07-25", "e": "2022-07-31", "st": ["Adamawa", "Borno"], "arr": 3878, "dep": None,
     "url": "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-286-25-31-july-2022"},
    {"n": 295, "s": "2022-09-26", "e": "2022-10-02", "st": ["Adamawa", "Borno"], "arr": 1854, "dep": None,
     "url": "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-295-26-september-02-october-2022"},
    {"n": 298, "s": "2022-10-17", "e": "2022-10-23", "st": ["Adamawa", "Borno"], "arr": 1437, "dep": None,
     "url": "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-298-17-23-october-2022"},
    {"n": 299, "s": "2022-10-24", "e": "2022-10-30", "st": ["Adamawa", "Borno"], "arr": 1097, "dep": None,
     "url": "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-299-24-30-october-2022"},
    {"n": 300, "s": "2022-10-31", "e": "2022-11-06", "st": ["Adamawa", "Borno", "Yobe"], "arr": 1486, "dep": None,
     "url": "https://dtm.iom.int/reports/nigeria-emergency-tracking-tool-report-300-31-october-6-november-2022"},
    {"n": 112, "s": "2019-03-25", "e": "2019-03-31", "st": ["Borno", "Adamawa"], "arr": 6628, "dep": 534,
     "url": "https://reliefweb.int/report/nigeria/nigeria-displacement-tracking-matrix-dtm-emergency-tracking-tool-ett-report-no-112-25"},
    {"n": 305, "s": "2022-12-05", "e": "2022-12-11", "st": ["Borno", "Adamawa"], "arr": None, "dep": None,
     "url": "https://dtm.iom.int/product-series/emergency-tracking-report"},
]
SEED_FLASH = [  # title, date, states, lgas, displaced/affected ind, hh, url
    ("IOM Nigeria Flash Report: Population Displacement – North-east Nigeria – Borno State (2 May 2025)", "2025-05-02", ["Borno"], [], None, None, "https://reliefweb.int/report/nigeria/iom-nigeria-flash-report-population-displacement-north-east-nigeria-borno-state-02-may-2025"),
    ("IOM Nigeria Flash Report: Population Displacement – North-east Nigeria – Borno State (19 May 2025)", "2025-05-19", ["Borno"], [], None, None, "https://reliefweb.int/report/nigeria/iom-nigeria-flash-report-population-displacement-north-east-nigeria-borno-state-19-may-2025"),
    ("North-east Nigeria — Borno State Flash Report 169 (15 September 2025)", "2025-09-15", ["Borno"], [], None, None, "https://reliefweb.int/report/nigeria/nigeria-north-east-nigeria-borno-state-flash-report-169-15-september-2025"),
    ("Farmer-herder and communal clashes – Apa, Gwer East, Katsina-Ala and Makurdi LGAs, Benue (29 May – 1 June 2025)", "2025-06-01", ["Benue"], ["Apa", "Gwer East", "Katsina-Ala", "Makurdi"], 943, 257, "https://dtm.iom.int/operations/north-central-and-north-west"),
    ("North Central & North West Flash Report #83 (27 December 2021 – 2 January 2022)", "2022-01-02", ["Benue", "Kaduna", "Katsina", "Zamfara"], [], None, None, "https://dtm.iom.int/operations/north-central-and-north-west"),
        ("North East Dikwa LGA – Borno State Flash Report (28 March 2021): arrivals 16–25 March, 3,876 since the 1–2 March attack", "2021-03-25", ["Borno"], ["Dikwa", "Jere"], 2296, None, "https://dtm.iom.int/reports/nigeria-%E2%80%94-north-east-dikwa-lga-%E2%80%94-borno-state-flash-report-28-march-2021"),
    ("North East Dikwa LGA – Borno State Flash Report (16–22 April 2021)", "2021-04-22", ["Borno"], ["Dikwa", "Jere", "Maiduguri"], 1551, None, "https://dtm.iom.int/reports/nigeria-%E2%80%94-north-east-dikwa-lga-%E2%80%94-borno-state-flash-report-16-22-april-2021"),
    ("North East Dikwa LGA – Borno State Flash Report (08–14 May 2021)", "2021-05-14", ["Borno"], ["Dikwa", "Jere", "Maiduguri"], 1346, None, "https://dtm.iom.int/reports/nigeria-—-north-east-dikwa-lga-—-borno-state-flash-report-08-14-may-2021"),
    ("North East Dikwa LGA – Borno State Flash Report (7 June 2021): 29 May–4 June", "2021-06-04", ["Borno"], ["Dikwa", "Jere", "Maiduguri"], 1390, None, "https://dtm.iom.int/reports/nigeria-%E2%80%94-north-east-dikwa-lga-%E2%80%94-borno-state-flash-report-7-june-2021"),
    ("North East Dikwa LGA – Borno State Flash Report (28 June 2021): attack of 24 June, 3 injured", "2021-06-24", ["Borno"], ["Dikwa", "Jere", "Maiduguri"], 1344, None, "https://dtm.iom.int/reports/nigeria-%E2%80%94-north-east-dikwa-lga-%E2%80%94-borno-state-flash-report-28-june-2021"),
    ("North East Dikwa LGA – Borno State Flash Report (12 July 2021): 3–9 July", "2021-07-09", ["Borno"], ["Dikwa", "Jere", "Maiduguri"], 1287, None, "https://dtm.iom.int/reports/nigeria-%E2%80%94-north-east-dikwa-lga-%E2%80%94-borno-state-flash-report-12-july-2021"),
    ("North East Dikwa LGA – Borno State Flash Report (26 July 2021): 17–23 July", "2021-07-23", ["Borno"], ["Dikwa", "Jere", "Konduga", "Maiduguri"], 1097, None, "https://dtm.iom.int/reports/nigeria-%E2%80%94-north-east-dikwa-lga-%E2%80%94-borno-state-flash-report-26-july-2021"),
    ("North East Dikwa LGA – Borno State Flash Report (03 August 2021): 24–30 July", "2021-07-30", ["Borno"], ["Dikwa", "Jere", "Konduga", "Maiduguri"], 1259, None, "https://dtm.iom.int/reports/nigeria-%E2%80%94-north-east-dikwa-lga-%E2%80%94-borno-state-flash-report-03-august-2021"),
    ("Farmer-herder and communal clashes – Apa, Gwer East, Katsina-Ala and Makurdi LGAs, Benue (29 May – 1 June 2025)", "2025-06-01", ["Benue"], ["Apa", "Gwer East", "Katsina-Ala", "Makurdi"], 943, 257, "https://dtm.iom.int/operations/north-central-and-north-west"),
    ("North Central & North West Flash Report #83 (27 December 2021 – 2 January 2022)", "2022-01-02", ["Benue", "Kaduna", "Katsina", "Zamfara"], [], None, None, "https://dtm.iom.int/operations/north-central-and-north-west"),
    ("Armed bandit attacks – Katsina State (21 May – 1 June 2026)", "2026-06-01", ["Katsina"], [], 3830, 517, "https://dtm.iom.int/nigeria"),
    ("North-central & North-west Flash Report 161 – Maru LGA, Zamfara (27 March 2024)", "2024-03-27", ["Zamfara"], ["Maru"], 230, 42, None),
    ("Farmers-herders clash – Tomanyiin, Nzorov ward, Guma LGA, Benue (20 January 2026)", "2026-01-20", ["Benue"], ["Guma"], 194, 43, "https://dtm.iom.int/nigeria?page=2"),
        ("NSAG attacks – Yarkasuwa Magajin Gari, Isa South ward, Isa LGA, Sokoto (19 January 2026)", "2026-01-19", ["Sokoto"], ["Isa"], None, None, "https://dtm.iom.int/nigeria?page=2"),
    ("Armed bandit attacks – Kankara and Musawa LGAs, Katsina (21 January 2026)", "2026-01-21", ["Katsina"], ["Kankara", "Musawa"], None, None, "https://dtm.iom.int/nigeria?page=2"),
    ("Armed bandit attack – DanGuro, Bungudu LGA, Zamfara (19 March 2026)", "2026-03-19", ["Zamfara"], ["Bungudu"], None, None, "https://dtm.iom.int/nigeria"),
    ]


# ---- Transhumance Tracking Tool (TTT): figures read from the published dashboards
TTT_EW = "https://dtm.iom.int/reports/nigeria-transhumance-tracking-tool-report-early-warning-dashboard-29-adamawa-and-taraba"
SEED_EWER = [  # number, series (states), period start, end, alerts, events, movements, extra, url
    (None, ["Adamawa"], "2022-04-01", "2022-04-30", 285, 257, 28, {}, "https://dtm.iom.int/taxonomy/term/14?page=250"),
    (12, ["Adamawa", "Taraba"], "2023-04-01", "2023-04-30", 155, 137, 18, {"lgas": 5, "wards": 31, "displacement_pct": 2, "casualty_pct": 27},
     "https://reliefweb.int/report/nigeria/transhumance-tracking-tool-ttt-adamawa-state-nigeria-early-warning-systems-dashboard-12-april-2023"),
    (18, ["Adamawa", "Taraba"], "2023-10-01", "2023-10-31", 317, 273, 44, {"displacement_pct": 5, "casualty_pct": 15},
     "https://reliefweb.int/report/nigeria/transhumance-tracking-tool-ttt-adamawa-and-taraba-state-nigeria-early-warning-systems-dashboard-18-october-2023"),
    (25, ["Adamawa", "Taraba"], "2024-05-01", "2024-05-31", None, None, None, {}, "https://dtm.iom.int/sites/g/files/tmzbdl1461/files/reports/Dashboard_TTT_040624_final.pdf"),
    (29, ["Adamawa", "Taraba"], "2024-09-01", "2024-09-30", 220, 201, 19, {"displacement_pct": 4, "casualty_pct": 13}, TTT_EW),
    (2, ["Katsina"], "2023-09-01", "2023-09-30", 32, None, None, {"events_pct": 100, "movements_pct": 0, "series": "Katsina – Batsari, Dan Musa, Jibia, Kankara"},
     "https://reliefweb.int/report/nigeria/transhumance-tracking-tool-ttt-batsari-dan-musa-jibia-and-kankara-lgas-katsina-state-nigeria-early-warning-dashboard-2-september-2023"),
    (4, ["Katsina"], "2023-11-01", "2023-11-30", None, None, None, {"events_pct": 85, "movements_pct": 15, "series": "Katsina – Batsari, Dan Musa, Jibia, Kankara"},
     "https://reliefweb.int/report/nigeria/transhumance-tracking-tool-ttt-katsina-state-nigeria-early-warning-dashboard-4-november-2023"),
    (4, ["Kaduna", "Katsina"], "2023-11-01", "2023-11-30", 57, None, None, {"events_pct": 35, "movements_pct": 65},
     "https://reliefweb.int/report/nigeria/transhumance-tracking-tool-ttt-kaduna-and-katsina-states-nigeria-early-warning-dashboard-4-november-2023"),
    (2, ["Kaduna", "Katsina"], "2023-09-01", "2023-09-30", 9, None, None, {},
     "https://dtm.iom.int/reports/nigeria-transhumance-tracking-tool-report-early-warning-dashboard-2-kachia-and-kaura-lgas"),
    (None, ["Kaduna", "Katsina"], "2024-03-01", "2024-03-31", 112, None, None, {"events_pct": 8, "movements_pct": 92}, "https://dtm.iom.int/product-series/other-26?page=8"),
    (None, ["Katsina"], "2024-03-01", "2024-03-31", 91, None, None, {}, "https://dtm.iom.int/product-series/other-26?page=8"),
    (None, ["Katsina"], "2024-04-01", "2024-04-30", 29, None, None, {"events_pct": 97, "movements_pct": 3}, "https://dtm.iom.int/product-series/other-26?page=8"),
    (18, ["Katsina", "Zamfara"], "2025-10-01", "2025-11-30", None, None, None, {}, "https://dtm.iom.int/nigeria?body=&f=&field_report_regional_report=All&page=1&title="),
    (19, ["Katsina", "Zamfara"], "2025-12-01", "2025-12-31", None, None, None, {}, "https://dtm.iom.int/nigeria?body=&f=&field_report_regional_report=All&page=1&title="),
    (20, ["Katsina", "Zamfara"], "2026-01-01", "2026-01-31", None, None, None, {}, "https://dtm.iom.int/nigeria?body=&f=&field_report_regional_report=All&page=1&title="),
    (21, ["Katsina", "Zamfara"], "2026-02-01", "2026-02-28", None, None, None, {}, "https://dtm.iom.int/reports/nigeria-north-central-north-west-flash-report-167-01-july-07-july-2024"),
    (22, ["Katsina", "Zamfara"], "2026-03-01", "2026-03-31", None, None, None, {}, "https://dtm.iom.int/nigeria"),
    (23, ["Katsina", "Zamfara"], "2026-04-01", "2026-04-30", None, None, None, {}, "https://dtm.iom.int/nigeria"),
    (24, ["Katsina", "Zamfara"], "2026-05-01", "2026-05-31", None, None, None, {}, "https://dtm.iom.int/nigeria"),
    (25, ["Katsina", "Zamfara"], "2026-06-01", "2026-06-30", None, None, None, {}, "https://dtm.iom.int/nigeria"),
    (26, ["Katsina", "Zamfara"], "2026-07-01", "2026-07-31", None, None, None, {}, "https://dtm.iom.int/reports/nigeria-transhumance-tracking-tool-report-early-warning-dashboard-2-kachia-and-kaura-lgas"),
    (27, ["Katsina", "Zamfara"], "2026-08-01", "2026-08-31", None, None, None, {}, "https://dtm.iom.int/reports/nigeria-north-west-nigeria-zamfara-state-flash-report-291-16-june-2026"),
]
SEED_FLOW = [  # number, period, {state: (herders, animals)}, counting points, extra, url
    (None, "2024-01-01", "2024-01-31", {"Kaduna": (3548, 72011), "Katsina": (873, 21422)}, 51, {},
     "https://reliefweb.int/report/nigeria/transhumance-tracking-tool-ttt-kaduna-and-katsina-states-nigeria-early-warning-dashboard-2-january-2024"),
    (None, "2024-02-01", "2024-02-29", {"Kaduna": (1423, 55211), "Katsina": (600, 13684)}, 34, {}, "https://dtm.iom.int/taxonomy/term/4?page=131"),
    (None, "2024-03-01", "2024-03-31", {"Kaduna": (1322, 43772), "Katsina": (197, 5013)}, 16, {"from_niger_pct": 3}, "https://dtm.iom.int/taxonomy/term/4?page=131"),
    (None, "2024-04-01", "2024-04-30", {"Kaduna": (506, 22153), "Katsina": (239, 4966)}, 14, {"from_niger_pct": 6}, "https://dtm.iom.int/product-series/other-26?page=8"),
    (8, "2024-07-01", "2024-07-31", {"Kaduna": (1817, 8662), "Katsina": (723, 2981)}, None, {"note": "herd count"}, "https://dtm.iom.int/nigeria?page=16"),
    (None, "2024-10-01", "2024-10-31", {"Kaduna": (271, None), "Katsina": (446, None)}, None, {}, "https://dtm.iom.int/report-product-series/flow-monitoring-dashboard-0"),
    (14, "2025-11-01", "2025-11-30", {}, None, {"states": ["Katsina", "Zamfara"]}, "https://dtm.iom.int/nigeria?body=&f=&field_report_regional_report=All&page=1&title="),
    (15, "2025-12-01", "2025-12-31", {}, None, {"states": ["Kaduna", "Katsina"]}, "https://dtm.iom.int/nigeria?body=&f=&field_report_regional_report=All&page=1&title="),
    (16, "2026-01-01", "2026-01-31", {}, None, {"states": ["Katsina", "Zamfara"]}, "https://dtm.iom.int/nigeria?body=&f=&field_report_regional_report=All&page=1&title="),
    (17, "2026-02-01", "2026-02-28", {"Katsina": (357, 8020), "Zamfara": (89, 2493)}, 36, {"nigerian_pct": 74, "nigerien_pct": 26, "within_nigeria_pct": 67, "cross_border_pct": 33},
     "https://dtm.iom.int/reports/nigeria-transhumance-tracking-tool-flow-monitoring-dashboard-17-katsina-and-zamfara-states"),
    (18, "2026-03-01", "2026-03-31", {}, None, {"states": ["Katsina", "Zamfara"]}, "https://dtm.iom.int/nigeria"),
    (19, "2026-04-01", "2026-04-30", {}, None, {"states": ["Katsina", "Zamfara"]}, "https://dtm.iom.int/nigeria"),
    (20, "2026-05-01", "2026-05-31", {}, 33, {"states": ["Katsina", "Zamfara"], "note": "Kaduna→Zamfara 1,528 animals / 45 herders; Nigeria→Niger 1,316 animals / 61 herders"},
     "https://dtm.iom.int/sites/g/files/tmzbdl1461/files/reports/TTT__Transhumance_Flow_Monitoring%20Report%2020_May%202026.pdf"),
    (21, "2026-06-01", "2026-06-30", {}, 21, {"states": ["Katsina", "Zamfara"], "note": "21 flow monitoring points in Katsina; no movement recorded in Zamfara"}, "https://dtm.iom.int/nigeria"),
    (22, "2026-07-01", "2026-07-31", {}, None, {"states": ["Katsina", "Zamfara"]}, "https://dtm.iom.int/nigeria"),
]


def ttt(items):
    """Early Warning (alerts = events + movements) and Flow Monitoring (herders, animals) per report:
    seed figures read from the dashboards, then every harvested TTT report the harvester read."""
    ew, fl = [], []
    for n, st, s, e, a, ev, mv, x, url in SEED_EWER:
        ew.append({"n": n, "states": st, "ps": s, "pe": e, "alerts": a, "events": ev, "movements": mv, **x, "url": url,
                   "title": f"TTT Early Warning Dashboard{(' ' + str(n)) if n else ''} — {' and '.join(st)} ({s[:7]})", "src": "report page"})
    for n, s, e, by, pts, x, url in SEED_FLOW:
        rows = [{"state": k, "herders": v[0], "animals": v[1]} for k, v in by.items()]
        fl.append({"n": n, "ps": s, "pe": e, "by_state": rows, "states": x.pop("states", list(by)), "points": pts,
                   "herders": sum(r["herders"] or 0 for r in rows) or None, "animals": sum(r["animals"] or 0 for r in rows) or None,
                   **x, "url": url, "title": f"TTT Flow Monitoring Dashboard{(' ' + str(n)) if n else ''} — {' and '.join(x.get('states', list(by)) or list(by))} ({s[:7]})",
                   "src": "report page"})
    have_ew = {(r["n"], r["ps"][:7], tuple(r["states"])) for r in ew}
    have_fl = {(r["n"], r["ps"][:7]) for r in fl}
    for it in items:
        f = it.get("figures", {})
        if it["component"] == "ewer" or ("early warning" in it["title"].lower() and "transhumance" in it["title"].lower()):
            k = (it.get("number"), (it.get("period_start") or it.get("published") or "")[:7], tuple(it.get("states") or []))
            if k in have_ew:
                continue
            ew.append({"n": it.get("number"), "states": it.get("states", []), "ps": it.get("period_start") or it.get("published"),
                       "pe": it.get("period_end"), "alerts": f.get("alerts"), "events": f.get("ttt_events"), "movements": f.get("ttt_movements"),
                       "url": it["url"], "pdf": (it.get("pdfs") or [None])[0], "title": it["title"], "src": "harvested"})
        elif it["component"] == "transhumance":
            k = (it.get("number"), (it.get("period_start") or it.get("published") or "")[:7])
            if k in have_fl:
                continue
            rows = [{"state": s, "herders": h, "animals": a} for s, (h, a) in (f.get("ttt_by_state") or {}).items()]
            fl.append({"n": it.get("number"), "ps": it.get("period_start") or it.get("published"), "pe": it.get("period_end"),
                       "by_state": rows, "states": it.get("states", []), "points": f.get("ttt_points"),
                       "herders": sum(r["herders"] or 0 for r in rows) or f.get("herders"), "animals": sum(r["animals"] or 0 for r in rows) or None,
                       "url": it["url"], "pdf": (it.get("pdfs") or [None])[0], "title": it["title"], "src": "harvested"})
    key = lambda r: (r.get("ps") or "")
    return {"ewer": sorted(ew, key=key), "flow": sorted(fl, key=key)}


FR = "https://dtm.iom.int/reports/"
SEED_FLASH2 = [  # number, incident from, to, states, lgas, displaced ind/hh, affected ind/hh, deaths, injured, communities, title, url
    (167, "2024-07-01", "2024-07-07", ["Sokoto"], ["Gwadabawa"], None, None, None, None, None, None, None, "North-central & North-west Flash Report 167", FR + "nigeria-north-central-north-west-flash-report-167-01-july-07-july-2024"),
    (178, "2024-09-30", "2024-09-30", ["Zamfara"], ["Maradun"], 32, None, 206, 42, None, None, 1, "North-central & North-west Conflict/Attack Flash Report 178", FR + "nigeria-north-central-north-west-conflictattack-flash-report-178-23-29-september-2024"),
    (None, "2025-09-23", "2025-09-23", ["Katsina"], ["Kankara"], 246, 35, 354, 50, None, None, 1, "Armed bandit attack – Zango, Kankara LGA, Katsina", "https://dtm.iom.int/product-series/flash-report-0?page=3"),
    (None, "2025-09-23", "2025-09-23", ["Zamfara"], ["Bungudu"], None, None, 2082, 416, None, None, 1, "Armed bandit attack – Samawa Babba, Bungudu LGA, Zamfara", "https://dtm.iom.int/product-series/flash-report-0?page=3"),
    (None, "2025-10-12", "2025-10-12", ["Zamfara"], ["Bukkuyum"], 21, None, 1806, 326, None, None, 1, "Armed bandit attack – Yashi, Bukkuyum LGA, Zamfara", "https://dtm.iom.int/product-series/flash-report-0?page=3"),
    (None, "2025-10-16", "2025-10-20", ["Zamfara"], ["Bukkuyum", "Kaura Namoda"], None, None, 2316, 462, None, None, 2, "Armed bandit attacks – Buzuzu Rayau (Bukkuyum) and Galadima (Kaura Namoda), Zamfara", "https://dtm.iom.int/product-series/flash-report-0?page=2"),
    (270, "2025-10-25", "2025-10-26", ["Zamfara"], ["Gummi", "Kaura Namoda"], None, None, None, None, None, None, 5, "North-west Nigeria — Zamfara State Flash Report 270", FR + "nigeria-north-west-nigeria-zamfara-state-flash-report-270-25-26-october-2025"),
    (275, "2026-01-18", "2026-01-18", ["Kaduna"], ["Kajuru"], None, None, 882, 146, None, None, 1, "North-west Nigeria — Kaduna State Flash Report 275 (Kurmin Wali)", FR + "nigeria-north-west-nigeria-kaduna-state-flash-report-275-18-january-2026"),
    (280, "2026-03-10", "2026-03-10", ["Zamfara"], [], None, None, None, None, None, None, None, "North-west Nigeria — Zamfara State Flash Report 280", "https://dtm.iom.int/reports/nigeria-north-central-north-west-flash-report-167-01-july-07-july-2024"),
    (281, "2026-03-13", "2026-03-13", ["Zamfara"], [], None, None, None, None, None, None, None, "Zamfara State Flash Report 281", "https://dtm.iom.int/reports/nigeria-north-central-north-west-flash-report-167-01-july-07-july-2024"),
    (284, "2026-04-15", "2026-04-15", ["Zamfara"], ["Kaura Namoda"], 722, 136, None, None, None, None, 1, "North-west Nigeria — Zamfara State Flash Report 284 (Gegeta)", FR + "nigeria-north-west-nigeria-zamfara-state-flash-report-284-15-april-2026"),
    (286, "2026-04-20", "2026-04-21", ["Zamfara"], ["Bukkuyum"], 1416, 288, None, None, None, None, 1, "North-west Nigeria — Zamfara State Flash Report 286 (Kairu; displaced to Kebbi State)", FR + "nigeria-north-west-nigeria-zamfara-state-flash-report-286-21-april-2026"),
    (287, "2026-04-21", "2026-04-21", ["Katsina"], [], None, None, None, None, None, None, None, "North-west Nigeria — Katsina State Flash Report 287", FR + "nigeria-transhumance-tracking-tool-report-baseline-mapping-batsari-dan-musa-jibia-and"),
    (None, "2026-04-23", "2026-04-24", ["Zamfara"], ["Bukkuyum", "Tsafe"], 1174, 237, None, None, None, None, 4, "Armed bandit attacks – Bukkuyum and Tsafe LGAs, Zamfara (23–24 April 2026)", "https://dtm.iom.int/taxonomy/term/15?page=9"),
    (289, "2026-05-16", "2026-05-16", ["Zamfara"], [], None, None, None, None, None, None, None, "North-west Nigeria — Zamfara State Flash Report 289", FR + "nigeria-north-west-nigeria-zamfara-state-flash-report-286-21-april-2026"),
    (290, "2026-05-21", "2026-06-01", ["Katsina"], [], 3830, 517, None, None, None, None, None, "North-west Nigeria — Katsina State Flash Report 290", FR + "nigeria-north-west-nigeria-katsina-state-flash-report-290-01-june-2026"),
    (291, "2026-06-15", "2026-06-15", ["Zamfara"], ["Gummi"], 484, 101, None, None, 3, 2, 1, "North-west Nigeria — Zamfara State Flash Report 291 (Gamo Gidan Bita)", FR + "nigeria-north-west-nigeria-zamfara-state-flash-report-291-16-june-2026"),
    (292, "2026-06-19", "2026-06-19", ["Zamfara"], [], None, None, None, None, None, None, None, "North-west Nigeria — Zamfara State Flash Report 292", FR + "nigeria-flood-situation-report-1-borno-state-13-september-2024"),
    (293, "2026-07-25", "2026-07-28", ["Katsina"], ["Dandume"], 252, 38, None, None, None, None, 3, "North-west Nigeria — Katsina State Flash Report 293", FR + "nigeria-north-west-nigeria-katsina-state-flash-report-293-29-30-july-2026"),
    (294, "2026-07-30", "2026-07-30", ["Zamfara"], [], None, None, None, None, None, None, None, "North-west Nigeria — Zamfara State Flash Report 294", FR + "nigeria-north-west-nigeria-katsina-state-flash-report-293-29-30-july-2026"),
    (295, "2026-08-07", "2026-08-09", ["Katsina"], [], None, None, None, None, None, None, None, "North-west Nigeria — Katsina State Flash Report 295", FR + "nigeria-north-west-nigeria-katsina-state-flash-report-290-01-june-2026"),
    (296, "2026-08-14", "2026-08-14", ["Katsina"], [], None, None, None, None, None, None, None, "North-west Nigeria — Katsina State Flash Report 296", FR + "nigeria-flood-situation-report-benue-state-18-september-2024"),
    (297, "2026-08-17", "2026-08-17", ["Zamfara"], [], None, None, None, None, None, None, None, "North-west Nigeria — Zamfara State Flash Report 297", FR + "nigeria-north-west-nigeria-zamfara-state-flash-report-291-16-june-2026"),
    (298, "2026-08-23", "2026-08-23", ["Katsina"], [], None, None, None, None, None, None, None, "North-west Nigeria — Katsina State Flash Report 298", FR + "nigeria-north-west-nigeria-zamfara-state-flash-report-291-16-june-2026"),
    (299, "2026-08-24", "2026-08-24", ["Zamfara"], [], None, None, None, None, None, None, None, "North-west Nigeria — Zamfara State Flash Report 299", FR + "nigeria-north-west-nigeria-zamfara-state-flash-report-291-16-june-2026"),
]


# children / women / men stated in flash reports (key = report number or incident start date)
FLASH_DEMOG = {291: (271, 129, 84), 293: (139, 66, 47), 178: (115, 50, 41), "2025-10-12": (1010, 438, 358),
               "2025-10-16": (1296, 562, 458), "2026-04-23": (655, 299, 220)}


def seed_items():
    """Reports read by hand from dtm.iom.int / ReliefWeb pages; the harvester's own copies replace them."""
    out = []
    for n, s, e, st, arr, idp, ret, farm, url in SEED_ETT:
        out.append({"title": f"Emergency Tracking Tool Report{(' ' + str(n)) if n else ''} — {', '.join(st)} ({s} to {e})", "url": url + ("" if n else f"#{st[0]}-{s}"),
                    "published": e, "component": "ett", "regions": ["North East" if st[0] != "Benue" else "North Central & North West"],
                    "states": st, "year": int(e[:4]), "month": e[:7], "week": None, "period_start": s, "period_end": e, "number": n,
                    "figures": {k: v for k, v in (("arrivals_ind", arr), ("ett_idp_ind", idp), ("ett_ret_ind", ret), ("ett_farm_ind", farm)) if v is not None},
                    "source": "report page (read by hand)", "pdfs": []})
    for x in SEED_ETT_EXTRA:
        f = {k: v for k, v in (("arrivals_ind", x["arr"]), ("departures_ind", x["dep"])) if v is not None}
        if x.get("lga_arrivals"):
            f["lga_arrivals"] = x["lga_arrivals"]
        out.append({"title": f"Emergency Tracking Tool Report {x['n']} ({x['s']} to {x['e']})", "url": x["url"], "published": x["e"],
                    "component": "ett", "regions": ["North East"], "states": x["st"], "lgas": list((x.get("lga_arrivals") or {}).keys()),
                    "year": int(x["e"][:4]), "month": x["e"][:7], "week": None, "period_start": x["s"], "period_end": x["e"],
                    "number": x["n"], "figures": f, "source": "report page (read by hand)", "pdfs": []})
    for n, s, e, st, lg, di, dh, ai, ah, dead, inj, comm, t, url in SEED_FLASH2:
        f = {k: v for k, v in (("displaced_ind", di), ("displaced_hh", dh), ("affected_ind", ai), ("affected_hh", ah), ("deaths", dead), ("injured", inj), ("communities", comm)) if v is not None}
        dm = FLASH_DEMOG.get(n) or FLASH_DEMOG.get(s)
        if dm:
            f["children"], f["women"], f["men"] = dm
        out.append({"title": t + (f" ({s}{' to ' + e if e != s else ''})"), "url": url + ("" if n and url.startswith(FR) and str(n) in url else f"#flash-{n or s}"),
                    "published": e, "component": "flash", "regions": ["North East" if st[0] in ("Borno", "Adamawa", "Yobe") else "North Central & North West"],
                    "states": st, "lgas": lg, "year": int(e[:4]), "month": e[:7], "week": None, "period_start": s, "period_end": e, "number": n,
                    "figures": f, "source": "report page (read by hand)", "pdfs": []})
    for t, d, st, lg, ind, hh, url in SEED_FLASH:
        m = __import__("re").search(r"Report (\d+)", t)
        out.append({"title": t, "url": url or f"https://dtm.iom.int/nigeria#{d}", "published": d, "component": "flash",
                    "regions": ["North East" if st[0] in ("Borno", "Adamawa", "Yobe", "Bauchi", "Gombe", "Taraba") else "North Central & North West"], "states": st, "lgas": lg, "year": int(d[:4]), "month": d[:7],
                    "week": None, "period_start": d, "period_end": d, "number": int(m.group(1)) if m else None,
                    "figures": {k: v for k, v in (("displaced_ind", ind), ("displaced_hh", hh)) if v is not None},
                    "source": "report page (read by hand)", "pdfs": []})
    return out


def ett_reports(items):
    rows = []
    for it in items:
        if it["component"] != "ett":
            continue
        rows.append({"n": it.get("number"), "t": it["title"], "u": it["url"], "pdf": (it.get("pdfs") or [None])[0],
                     "p": it.get("published"), "ps": it.get("period_start"), "pe": it.get("period_end"),
                     "w": it.get("week"), "m": it.get("month"), "s": it.get("states", []), "l": it.get("lgas", []),
                     "arr": it.get("figures", {}).get("arrivals_ind"), "arr_hh": it.get("figures", {}).get("arrivals_hh"),
                     "dep": it.get("figures", {}).get("departures_ind"),
                     "idp": it.get("figures", {}).get("ett_idp_ind"), "ret": it.get("figures", {}).get("ett_ret_ind"), "farm": it.get("figures", {}).get("ett_farm_ind")})
    return sorted(rows, key=lambda r: (r["ps"] or r["p"] or ""))


def datasets_list(items):
    rows, man = [], {}
    mf = ROOT / "data" / "raw" / "online" / "manifest.json"
    if mf.exists():
        man = json.loads(mf.read_text())
    for it in items:
        if it.get("kind") == "dataset" or "/datasets/" in it["url"] or "humdata.org" in it["url"]:
            m = man.get(it["url"], {})
            rows.append({"t": it["title"], "u": it["url"], "p": it.get("published"), "c": it["component"], "rd": it.get("round"),
                         "r": it.get("regions", []), "src": it.get("source"),
                         "files": [{"name": f["file"], "link": f["link"], "local": "files/online/" + f["file"]} for f in m.get("files", [])],
                         "restricted": m.get("restricted", False)})
    seen = {r["u"] for r in rows}
    for page, m in man.items():
        if page in seen:
            continue
        rds = sorted({f.get("round") for f in m.get("files", []) if f.get("round")})
        rows.append({"t": m.get("title") or page, "u": page, "p": m.get("checked"), "c": "mt_atlas",
                     "rd": (rds[0] if len(rds) == 1 else None), "rounds": rds,
                     "r": ["North Central & North West" if m.get("region") == "nwnc" else "North East"], "src": m.get("source", "dtm.iom.int"),
                     "files": [{"name": f["file"], "link": f["link"], "local": "files/online/" + f["file"], "round": f.get("round")} for f in m.get("files", [])],
                     "restricted": m.get("restricted", False)})
    return sorted(rows, key=lambda r: r["p"] or "")


def copy_files():
    """Copy every data file to dist/files so the live site can link to it."""
    out = []
    for folder, sub in ((ROOT / "data" / "raw", ""), (ROOT / "data" / "raw" / "online", "online/")):
        if not folder.exists():
            continue
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in (".xlsx", ".xls", ".csv"):
                dest = DIST / "files" / sub / f.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(f, dest)
                out.append({"name": f.name, "path": "files/" + sub + f.name, "kb": round(f.stat().st_size / 1024),
                            "origin": "downloaded from the website" if sub else "attached data file"})
    return out


def biometric(seed, items):
    """Seed figures plus every harvested biometric registration report with LGA figures."""
    out = list(seed)
    urls = {b["url"] for b in out}
    for it in sorted(items, key=lambda x: x.get("published") or ""):
        f = it.get("figures", {})
        if it["component"] != "biometric" or not f.get("bio_lgas") or it["url"] in urls:
            continue
        for st in (it.get("states") or ["Benue"])[:1]:
            out.append({"state": st, "date": it.get("published"), "hh": sum(x["hh"] or 0 for x in f["bio_lgas"]),
                        "ind": sum(x["ind"] or 0 for x in f["bio_lgas"]), "lgas": f["bio_lgas"],
                        "locations": f.get("bio_locations"), "vulnerable": f.get("bio_vulnerable"),
                        "title": it["title"], "url": it["url"]})
    return out


def slim(it):
    return {"t": it["title"], "u": it["url"], "p": it.get("published"), "c": it["component"],
            "r": it.get("regions", []), "s": it.get("states", []), "y": it.get("year"), "m": it.get("month"),
            "w": it.get("week"), "ps": it.get("period_start"), "pe": it.get("period_end"),
            "rd": it.get("round"), "n": it.get("number"), "f": it.get("figures", {}), "src": it.get("source"),
            "k": it.get("kind", "report"), "l": it.get("lgas", []), "pdf": (it.get("pdfs") or [None])[0]}


def main():
    DIST.mkdir(exist_ok=True)
    base = json.loads(BASE.read_text())
    items = load_harvest()
    have = {(i["component"], i.get("number"), i.get("period_start"), tuple(i.get("states") or [])) for i in items}
    for s_ in seed_items():
        if (s_["component"], s_.get("number"), s_.get("period_start"), tuple(s_["states"])) not in have:
            items.append(s_)
    apply_api(base)
    link_rounds(base["north_east"], items, NE)
    link_rounds(base["nc_nw"], items, NC)
    xr = crossref(base)          # compare before the report figures replace the data-file ones
    report_first(base)
    idp_split(base)
    data = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "harvest_count": len(items), **base,
            "publications": [slim(x) for x in items],
            "crossref": xr,
            "ett_reports": ett_reports(items),
            "datasets": datasets_list(items),
            "files": copy_files(),
            "biometric": biometric(base.get("biometric", []), items),
            "ttt": ttt(items),
            "ett_crosscheck": ett_crosscheck(base["ett"], items)}
    payload = json.dumps(data, separators=(",", ":"))
    (DIST / "data.json").write_text(payload)
    html = (SITE / "index.html").read_text().replace("/*__DATA__*/null", payload.replace("</", "<\\/"))
    vend = SITE / "vendor"
    html = html.replace("/*__LEAFLET_JS__*/", (vend / "leaflet.js").read_text().replace("</script", "<\\/script"))
    html = html.replace("/*__LEAFLET_CSS__*/", (vend / "leaflet.css").read_text())
    html = html.replace("/*__GEO__*/null", (vend / "nigeria_states.geojson").read_text())
    (DIST / "index.html").write_text(html)
    for f in ("sw.js", "manifest.webmanifest", "_headers"):
        if (SITE / f).exists():
            shutil.copy(SITE / f, DIST / f)
    (DIST / ".nojekyll").write_text("")
    print(f"built dist/ – {len(items)} publications, {len(data['crossref'])} cross-check rows, index.html {len(html)/1024:.0f} KB")


if __name__ == "__main__":
    main()
