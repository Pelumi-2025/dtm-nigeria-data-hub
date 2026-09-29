"""
Builds data/baseline.json from every data file in data/raw/.

File naming (drop new files in data/raw/ with these prefixes — any number of files):
  ne_mobility_*.xlsx|csv     North East mobility tracking datasets  (any rounds, e.g. R53–R65)
  nwnc_mobility_*.xlsx|csv   North Central & North West datasets   (any rounds, e.g. R20–R35)
  ett_*.xlsx|csv             Emergency Tracking Tool movement records
  flood_*.xlsx|csv           Post-flood community assessments
  incidents_*.xlsx|csv       Incident / flash datasets (optional)
  round_figures.csv          Headline figures typed in from reports (optional):
                             region,round,date,idp_ind,idp_hh,ret_ind,ret_hh,idp_locations,camps,hc_locations,ret_locations,source

Columns are matched by name (see SYN below), so datasets with slightly different
headers still load. When two files contain the same round, the file whose name
sorts last wins (e.g. ne_mobility_r47_r52.xlsx overrides ne_mobility_r1_r46.xlsx for shared rounds).
"""
import json
import re
from pathlib import Path

import pandas as pd

from config import SUB_REGION, region_of_state

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "baseline.json"
REG_NE, REG_NC = "North East", "North Central & North West"

# ---------------------------------------------------------------- column synonyms
SYN = {
    "round": ["rounds", "round", "round number", "round no", "dtm round"],
    "published": ["published months", "published month", "publication date", "published", "report date"],
    "period": ["assessment periods", "assessment period", "data collection period"],
    "region": ["region", "zone", "geopolitical zone"],
    "state": ["state name", "state of displacement", "state", "admin1name", "admin 1", "admin1"],
    "lga": ["lga name", "lga", "admin2name", "admin 2", "admin2", "local government area"],
    "ward": ["ward name", "ward", "admin3name", "admin 3", "admin3"],
    "location": ["location name", "site name", "location", "site", "community name", "community"],
    "loctype": ["location type", "site type", "type of location", "settlement type", "population type"],
    "ind": ["estimate ind ward", "estimate ind", "estimated number of idp", "estimated number of idps",
            "idp individuals", "number of individuals", "individuals", "ind", "total individuals",
            "estimated number of returnees", "returnee individuals", "persons"],
    "hh": ["estimate hh ward", "estimate hh", "estimated household number", "estimated number of households",
           "households", "hh", "number of households", "idp households", "returnee households"],
    "category": ["idp category", "category", "reason for displacement", "cause of displacement", "#cause+type", "displacement reason"],
}
# HXL hashtags used in DTM files on HDX
HXL = {"round": ["#round", "#round+num", "#date+round"], "state": ["#adm1+name", "#adm1+name+en"], "lga": ["#adm2+name", "#adm2+name+en"],
       "ward": ["#adm3+name", "#adm3+name+en"], "location": ["#loc+name", "#site+name", "#loc+name+en"],
       "loctype": ["#loc+type", "#site+type"], "ind": ["#affected+idps+ind", "#affected+ind", "#affected+idps+ind+total", "#affected+returnees+ind"],
       "hh": ["#affected+idps+hh", "#affected+hh", "#affected+returnees+hh"]}


def norm(s):
    return re.sub(r"[\s_]+", " ", str(s).strip().lower())


def colmap(df):
    cols = {norm(c): c for c in df.columns}
    out = {}
    for key, names in SYN.items():
        for n in names + HXL.get(key, []):
            if n in cols:
                out[key] = cols[n]
                break
    # fuzzy fallback for files whose headers differ (e.g. "Total number of IDP individuals")
    for c_norm, c in cols.items():
        if "ind" not in out and re.search(r"(individual|\bind\b|persons)", c_norm) and not re.search(r"(male|female|%|percent|age|year)", c_norm):
            out["ind"] = c
        if "hh" not in out and re.search(r"(household|\bhh\b)", c_norm) and not re.search(r"(%|percent|head|size)", c_norm):
            out["hh"] = c
        if "state" not in out and re.search(r"\bstate\b", c_norm) and "origin" not in c_norm and "previous" not in c_norm:
            out["state"] = c
        if "lga" not in out and re.search(r"\blga\b|local government", c_norm) and "origin" not in c_norm and "previous" not in c_norm:
            out["lga"] = c
        if "ward" not in out and re.search(r"\bward\b", c_norm) and "origin" not in c_norm:
            out["ward"] = c
        if "location" not in out and re.search(r"(site|location|camp) name|name of (site|location|camp)", c_norm):
            out["location"] = c
        if "loctype" not in out and re.search(r"(site|location|settlement) type|type of (site|location)", c_norm):
            out["loctype"] = c
    return out


def rnum(x):
    m = re.search(r"(\d+)", str(x))
    return int(m.group(1)) if m else None


def tstate(s):
    s = re.sub(r"\s+", " ", str(s).strip()).title()
    return {"Fct": "FCT", "Abuja": "FCT", "Nassarawa": "Nasarawa", "Akwa-Ibom": "Akwa Ibom"}.get(s, s)


def tlga(s):
    s = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", str(s).strip().upper()))
    return s.title().replace("'S", "'s")


def I(x):
    return None if x is None or pd.isna(x) else int(round(float(x)))


def fix_header(df):
    """DTM files sometimes start with a title row, or carry an HXL row under the headers.
    Find the real header row within the first 10 rows."""
    def ok(cols):
        n = [norm(c) for c in cols]
        return any("state" in c or "adm1" in c for c in n) and any(re.search(r"lga|adm2|ind|individual|household", c) for c in n)
    if ok(df.columns):
        out = df
    else:
        out = df
        for i in range(min(10, len(df))):
            row = [str(v) for v in df.iloc[i].tolist()]
            if ok(row):
                out = df.iloc[i + 1:].copy()
                out.columns = [str(v) for v in df.iloc[i].tolist()]
                break
    # drop an HXL hashtag row directly under the header
    if len(out) and all(str(v).startswith("#") or str(v) in ("nan", "None", "") for v in out.iloc[0].tolist()):
        hx = [str(v) for v in out.iloc[0].tolist()]
        out = out.iloc[1:].copy()
        # keep hashtags as a fallback naming when headers are blank
        out.columns = [c if str(c).strip() and not str(c).startswith("Unnamed") else h for c, h in zip(out.columns, hx)]
    return out.reset_index(drop=True)


def read_any(path):
    if path.suffix.lower() == ".csv":
        try:
            return {"csv": fix_header(pd.read_csv(path, low_memory=False))}
        except UnicodeDecodeError:
            return {"csv": fix_header(pd.read_csv(path, encoding="latin-1", low_memory=False))}
    book = pd.read_excel(path, sheet_name=None)
    return {k: fix_header(v) for k, v in book.items()}


def packed(rows, cols):
    """Compact table: {cols:[...], rows:[[...]]} – about a third of the size of a list of dicts."""
    return {"cols": cols, "rows": [[r.get(c) for c in cols] for r in rows]}


# ================================================================= mobility tracking
LGA_COLS = ["round", "state", "lga",
            "idp_hh", "idp_ind", "camp_hh", "camp_ind", "hc_hh", "hc_ind", "ret_hh", "ret_ind",
            "idp_wards", "camp_wards", "hc_wards", "ret_wards",
            "idp_locs", "camp_locs", "hc_locs", "ret_locations"]
REASON_COLS = ["round", "state", "group", "reason", "hh", "ind"]
PLACE_COLS = ["state", "lga", "ward", "loc", "g", "rounds"]


def to_ranges(nums):
    """[1,2,3,5,7,8] -> '1-3,5,7-8' (compact list of rounds a place was assessed in)."""
    nums = sorted(set(int(n) for n in nums))
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(out)

# Reason codes used in the DTM files: 1 Insurgency, 2 Communal Clashes,
# 3 Farmers-Herders Clashes, 4 Natural/Climate-related Disaster.
R_INS, R_COM, R_FH, R_NAT, R_BAN, R_OTH = ("Insurgency", "Communal Clashes", "Farmers-Herders Clashes",
                                           "Natural/Climate-related Disaster", "Armed Banditry/Kidnapping", "Other/Not stated")
REASON_ORDER = [R_INS, R_COM, R_FH, R_NAT, R_BAN, R_OTH]


def reason_label(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    t = str(v).strip()
    if re.fullmatch(r"\d+(\.0)?", t):
        return {1: R_INS, 2: R_COM, 3: R_FH, 4: R_NAT}.get(int(float(t)), R_OTH)
    low = re.sub(r"\s+", " ", t.lower().replace("displace by", "").replace("displaced by", "")).strip()
    if not low or low in ("nan", "none"):
        return None
    if "insurg" in low or "non-state" in low or "nsag" in low:
        return R_INS
    if "herd" in low or "header" in low or "farmer" in low:
        return R_FH
    if "bandit" in low or "kidnap" in low:
        return R_BAN
    if "communal" in low or "clash" in low:
        return R_COM
    if "natural" in low or "climate" in low or "flood" in low or "disaster" in low or "wind" in low:
        return R_NAT
    return R_OTH


REASON_SPLIT_COLS = {R_INS: "reason_insurg", R_COM: "reason_clash", R_NAT: "reason_disaster", R_OTH: "reason_others"}


def load_frames(prefix):
    """Every sheet with round/state/individuals columns, from attached files (data/raw)
    and from datasets the harvester downloaded from the websites (data/raw/online)."""
    frames = []
    for origin, folder in (("attached", RAW), ("online", RAW / "online")):
        files = sorted(list(folder.glob(f"{prefix}*.xlsx")) + list(folder.glob(f"{prefix}*.csv"))) if folder.exists() else []
        for f in files:
            try:
                book = read_any(f)
            except Exception as e:
                print(f"  cannot read {f.name}: {e}")
                continue
            for sheet, df in book.items():
                if df.empty:
                    continue
                cm = colmap(df)
                if not {"state", "ind"} <= cm.keys():
                    continue
                if "round" not in cm:
                    m = re.search(r"r(?:ound)?[_ -]?(\d{1,3})", f.stem.lower())
                    if not m:
                        continue
                    df = df.assign(__round=int(m.group(1)))
                    cm["round"] = "__round"
                lt = df[cm["loctype"]].astype(str).str.lower() if "loctype" in cm else pd.Series("", index=df.index)
                sn = norm(sheet) + " " + norm(f.stem)
                ret = lt.str.contains("return") | ("return" in norm(sheet))
                camp = (~ret) & (lt.str.contains("camp") | ("camp" in norm(sheet)))
                fname = norm(f.stem)
                if "return" in fname and "idp" not in fname:
                    ret = pd.Series(True, index=df.index)
                    camp = pd.Series(False, index=df.index)
                d = pd.DataFrame({
                    "round": df[cm["round"]].map(rnum), "state": df[cm["state"]].map(tstate),
                    "lga": df[cm["lga"]].map(tlga) if "lga" in cm else "Unknown",
                    "ward": df[cm["ward"]].astype(str).str.strip().str.upper() if "ward" in cm else "",
                    "location": df[cm["location"]].astype(str).str.strip().str.upper() if "location" in cm else "",
                    "ind": pd.to_numeric(df[cm["ind"]], errors="coerce").fillna(0),
                    "hh": pd.to_numeric(df[cm["hh"]], errors="coerce").fillna(0) if "hh" in cm else 0,
                    "published": pd.to_datetime(df[cm["published"]], errors="coerce") if "published" in cm else pd.NaT,
                    "period": df[cm["period"]].astype(str) if "period" in cm else None,
                    "reason": df[cm["category"]].map(reason_label) if "category" in cm else None,
                    "ret": ret.values, "camp": camp.values, "file": f.name, "origin": origin,
                    "other_site": ((~ret) & lt.str.contains("integrat|relocat|resettl")).values,
                    "kind": (re.search(r"(baseline|site|location|master|needs|return)", fname) or [None, "file"])[1] if origin == "online" else "attached",
                })
                for lab, pre in REASON_SPLIT_COLS.items():
                    d["rs_" + pre] = pd.to_numeric(df[pre + "_ind"], errors="coerce").fillna(0) if pre + "_ind" in df.columns else 0
                    d["rsh_" + pre] = pd.to_numeric(df[pre + "_hh"], errors="coerce").fillna(0) if pre + "_hh" in df.columns else 0
                d = d.dropna(subset=["round"])
                d = d[d["state"].str.len() > 1]
                d = d[~d["state"].str.lower().isin(["total", "grand total", "nan", "none"])]
                if d.empty:
                    continue
                d["round"] = d["round"].astype(int)
                frames.append(d)
                rs = sorted(d["round"].unique())
                print(f"  [{origin}] {f.name}:{sheet} -> {len(d)} rows, R{rs[0]}–R{rs[-1]}")
    return pd.concat(frames, ignore_index=True) if frames else None


def choose_sources(d, reference):
    """Per round and population (IDP / returnee), use the data file downloaded from the websites
    (dtm.iom.int / HDX) when its total agrees within 5% with the attached file or the published
    total. Several online files of the same kind (e.g. camps + host communities) are combined;
    different kinds of the same round (baseline, site, location assessment) are never added up —
    the kind closest to the reference wins. Otherwise the attached file is used."""
    keep, notes = [], {}
    for (rn, ret), g in d.groupby(["round", "ret"]):
        att = g[g.origin == "attached"]
        att_files = sorted(att.file.unique())
        att = att[att.file == att_files[-1]] if att_files else att
        ref = att.ind.sum() if len(att) else reference.get((rn, bool(ret)))
        onl = g[g.origin == "online"]
        best, best_gap = None, None
        for kind, gk in onl.groupby("kind"):
            v = gk.ind.sum()
            gap = abs(v - ref) / ref if ref else 0
            if ref is None or gap <= 0.05:
                if best is None or gap < best_gap:
                    best, best_gap = gk, gap
            else:
                notes.setdefault(int(rn), []).append(f"online {kind} file(s) not used for {'returnees' if ret else 'IDPs'}: {v:,.0f} vs {ref:,.0f}")
        if best is not None:
            keep.append(best)
        elif len(att):
            keep.append(att)
    return pd.concat(keep, ignore_index=True), notes


def load_mobility(prefix, reference):
    d = load_frames(prefix)
    if d is None:
        return [], {}, [], {}, []
    d, notes = choose_sources(d, reference)
    idp, ret = d[~d.ret].copy(), d[d.ret].copy()
    split = bool(idp.camp.any())          # does this region's data separate camps from host communities?
    # host communities exclude camps and the integrated / relocated sites reported separately since R50
    hc, camp = (idp[(~idp.camp) & (~idp.other_site)], idp[idp.camp]) if split else (idp.iloc[0:0], idp.iloc[0:0])
    key = ["round", "state", "lga"]

    def grp(df, pre, loc_name):
        if df.empty:
            return pd.DataFrame(columns=key)
        x = df.assign(w=df.ward, l=df.ward + "|" + df.location)
        return x.groupby(key).agg(**{pre + "_hh": ("hh", "sum"), pre + "_ind": ("ind", "sum"),
                                     pre + "_wards": ("w", "nunique"), loc_name: ("l", "nunique")}).reset_index()

    m = grp(idp, "idp", "idp_locs")
    for df, pre, ln in ((camp, "camp", "camp_locs"), (hc, "hc", "hc_locs"), (ret, "ret", "ret_locations")):
        m = m.merge(grp(df, pre, ln), how="outer", on=key)
    lga_rows = [{"round": int(a["round"]), "state": a["state"], "lga": a["lga"],
                 **{k: (I(a[k]) if k in a and not pd.isna(a[k]) and a[k] != 0 else None) for k in LGA_COLS[3:]}}
                for a in m.to_dict("records")]

    meta = {}
    for rn, g in d.groupby("round"):
        pub = g["published"].dropna()
        per = [p for p in (g["period"].dropna() if g["period"] is not None else []) if p not in ("None", "nan")]
        gi, gr = g[~g.ret], g[g.ret]
        origin = sorted(set(g.origin))
        meta[int(rn)] = {
            "round": int(rn), "date": pub.iloc[0].strftime("%Y-%m") if len(pub) else None,
            "period": (str(per[0]).strip().rstrip(".") if per and not re.match(r"^\d{4}-\d{2}-\d{2}", str(per[0])) else None),
            "idp_hh": I(gi.hh.sum()) if len(gi) else None, "idp_ind": I(gi.ind.sum()) if len(gi) else None,
            "camp_hh": I(gi[gi.camp].hh.sum()) if gi.camp.any() else None, "camp_ind": I(gi[gi.camp].ind.sum()) if gi.camp.any() else None,
            "hc_hh": I(gi[(~gi.camp) & (~gi.other_site)].hh.sum()) if gi.camp.any() else None,
            "hc_ind": I(gi[(~gi.camp) & (~gi.other_site)].ind.sum()) if gi.camp.any() else None,
            "other_site_ind": I(gi[gi.other_site].ind.sum()) if gi.other_site.any() else None,
            "ret_hh": I(gr.hh.sum()) if len(gr) else None, "ret_ind": I(gr.ind.sum()) if len(gr) else None,
            "idp_lgas": int((gi.state + gi.lga).nunique()) if len(gi) else None,
            "idp_wards": int((gi.state + gi.lga + gi.ward).nunique()) if len(gi) else None,
            "idp_sites": int((gi.state + gi.lga + gi.ward + gi.location).nunique()) if len(gi) and (gi.location != "").any() else None,
            "states": int(g.state.nunique()), "source": "dataset",
            "data_source": " + ".join("data file downloaded from the website" if o == "online" else "attached data file" for o in origin),
            "files": sorted(set(g.file)), "data_notes": notes.get(int(rn)),
        }
    # reasons: per round x state x population group, households and individuals
    rrows = []
    for grp_name, df in (("IDPs", idp), ("Returnees", ret)):
        if df.empty:
            continue
        coded = df[df["reason"].notna()] if df["reason"] is not None else df.iloc[0:0]
        for (rn, st, rs), g in coded.groupby(["round", "state", "reason"]):
            rrows.append({"round": int(rn), "state": st, "group": grp_name, "reason": rs, "hh": I(g.hh.sum()), "ind": I(g.ind.sum())})
        rest = df[df["reason"].isna()] if df["reason"] is not None else df
        for (rn, st), g in rest.groupby(["round", "state"]):
            for lab, pre in REASON_SPLIT_COLS.items():
                v, h = g["rs_" + pre].sum(), g["rsh_" + pre].sum()
                if v:
                    rrows.append({"round": int(rn), "state": st, "group": grp_name, "reason": lab, "hh": I(h), "ind": I(v)})
    # every place (LGA / ward / location) with the rounds it was assessed in, per population group,
    # so distinct LGAs, wards and locations can be counted across any set of rounds
    places = []
    for gname, df in ((("hc", hc), ("camp", camp)) if split else (("idp", idp),)) + (("ret", ret),):
        if df.empty:
            continue
        for (st, lg, wd, lc), g in df.groupby(["state", "lga", "ward", "location"])["round"]:
            places.append({"state": st, "lga": lg, "ward": wd, "loc": lc if lc not in ("", "NAN", "NONE") else "",
                           "g": gname, "rounds": to_ranges(g.unique())})
    return lga_rows, meta, rrows, notes, places


# Headline figures stated in published reports: rounds with no dataset yet, plus
# site-level location counts (datasets are ward-level, reports count sites).
REPORTED = [
    {"region": REG_NE, "round": 42, "date": "2022-07", "ret_ind": 1983130, "ret_hh": 323277,
     "source": "https://dtm.iom.int/reports/nigeria-north-east-mobility-tracking-idp-and-returnee-atlas-round-42-july-2022"},
    {"region": REG_NE, "round": 44, "idp_locations": 2482,
     "source": "https://data.humdata.org/dataset/nigeria-site-assessment-data"},
    {"region": REG_NE, "round": 47, "date": "2024-06", "idp_ind": 2271987, "idp_hh": 468013, "ret_ind": 2093604,
     "idp_locations": 2299, "camps": 266, "hc_locations": 2033,
     "source": "https://dtm.iom.int/data-product-series/site-assessment-2"},
    {"region": REG_NE, "round": 48, "date": "2024-09", "idp_ind": 2255595, "idp_hh": 465669, "ret_ind": 2110477,
     "ret_hh": 348341, "idp_locations": 2264, "camps": 250, "hc_locations": 2014,
     "source": "https://dtm.iom.int/data-product-series/site-assessment-2"},
    {"region": REG_NE, "round": 49, "date": "2024-11", "idp_ind": 2252348, "idp_hh": 465935, "ret_ind": 2129325,
     "idp_locations": 2254, "camps": 259, "hc_locations": 1995, "source": "https://dtm.iom.int/taxonomy/term/2261",
     "reasons_pct": {"Insurgency": 92, "Communal clashes": 6}},
    {"region": REG_NE, "round": 50, "date": "2025-04", "idp_ind": 2292477, "idp_hh": 475234, "ret_ind": 2189318,
     "ret_hh": 361953, "idp_locations": 2282, "camps": 248, "hc_locations": 1774, "ret_locations": 846,
     "note": "Also 64 relocated and 196 integrated IDP sites; 3,128 locations assessed in total",
     "source": "https://dtm.iom.int/component/mobility-tracking?page=62"},
    {"region": REG_NE, "round": 51, "date": "2025-10", "idp_ind": 2333190, "ret_ind": 2253304, "idp_locations": 2324,
     "idp_hh": 478229,
     "note": "IDPs = host communities 1,300,127 + camps 912,881 + integrated 76,972 + relocated 43,210",
     "source": "https://dtm.iom.int/product-series/displacement-report-15"},
    {"region": REG_NE, "round": 52, "date": "2026-06",
     "note": "IDP and Returnee Atlas R52 (June 2026, assessed Jan–Mar 2026) and North-East Displacement Report R52 (March 2026) are published; figures are read by the harvester",
     "extra_links": ["https://dtm.iom.int/taxonomy/term/2096"],
     "source": "https://reliefweb.int/report/nigeria/north-east-nigeria-idp-and-returnee-atlas-mobility-tracking-round-52-june-2026"},
    {"region": REG_NC, "round": 14, "idp_locations": 1733,
     "source": "https://reliefweb.int/report/nigeria/idp-atlas-mobility-tracking-north-central-and-north-west-nigeria-june-2024"},
]
# Figures quoted in published reports for rounds that ARE in the datasets – used only to cross-check.
REPORT_CHECKS = [
    (REG_NE, 1, 389281, 60232, None, None, "https://dtm.iom.int/dtm_download_track/471?file=1&type=node&id=576"),
    (REG_NE, 25, 2026602, 388767, 1642696, 273970, "https://data.humdata.org/dataset/nigeria-baseline-data-iom-dtm"),
    (REG_NE, 36, 2184254, None, None, None, "https://data.humdata.org/dataset/nigeria-baseline-data-iom-dtm"),
    (REG_NE, 8, 2241484, 334608, None, None, "https://webarchive.archive.unhcr.org/20180621143605mp_/https://data2.unhcr.org/fr/documents/download/49174"),
    (REG_NE, 11, 2093030, 370389, None, None, "https://webarchive.archive.unhcr.org/20171225035734mp_/https://data2.unhcr.org/en/documents/download/50878"),
    (REG_NE, 16, 1884331, 339362, None, None, "https://data.unhcr.org/en/documents/details/58191"),
    (REG_NE, 30, 2039092, 420994, None, None, "https://data.humdata.org/dataset/nigeria-baseline-data-iom-dtm/resource/759e0995-e011-456a-8a7e-1120e8930924"),
    (REG_NE, 32, 2088124, 429442, None, None, "https://reliefweb.int/report/nigeria/dtm-nigeria-baseline-dashboard-round-32-june-2020"),
    (REG_NE, 41, 2197824, 452219, None, None, "https://data.humdata.org/dataset/nigeria-baseline-data-iom-dtm/resource/f2d456b1-b5d1-4d10-9b4a-d7cfe4013011"),
    (REG_NE, 42, 2455190, 501758, 1983130, 323277, "https://dtm.iom.int/reports/nigeria-north-east-mobility-tracking-idp-and-returnee-atlas-round-42-july-2022"),
    (REG_NE, 43, 2375661, 483467, 2100180, None, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 44, 2388703, 487978, 2110039, 346166, "https://dtm.iom.int/reports/nigeria-north-east-mobility-tracking-round-44-idp-and-returnee-atlas-april-2023"),
    (REG_NE, 44, 2388703, 488163, 2110039, 346166, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 45, 2295534, 471346, 2075257, 341895, "https://dtm.iom.int/product-series/displacement-report-15?page=1"),
    (REG_NE, 46, 2305335, 472239, 2083835, None, "https://reliefweb.int/report/nigeria/north-east-nigeria-mobility-tracking-round-46-idp-and-returnee-atlas-december-2023"),
    (REG_NE, 37, 2191193, 445852, None, None, "https://data.humdata.org/dataset/nigeria-baseline-data-iom-dtm"),
    (REG_NE, 38, 2182613, 444781, None, None, "https://data.humdata.org/dataset/nigeria-baseline-data-iom-dtm"),
    (REG_NE, 39, 2200357, 452363, 1943445, 313834, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 40, 2171652, 446740, 1960558, None, "https://data.humdata.org/dataset/nigeria-baseline-data-iom-dtm"),
    (REG_NC, 10, 1087875, 180307, None, None, "https://dtm.iom.int/reports/nigeria-north-central-and-north-west-mobility-tracking-round-10-idp-atlas-october-2022"),
    (REG_NC, 11, 1190293, 191688, None, None, "https://dtm.iom.int/reports/nigeria-north-central-and-north-west-displacement-report-11-march-2023"),
    (REG_NC, 13, 1092196, 183437, None, None, "https://dtm.iom.int/reports/nigeria-north-central-and-north-west-round-13-idp-atlas-march-2024"),
    (REG_NC, 14, 1302443, 219445, None, None, "https://reliefweb.int/report/nigeria/idp-atlas-mobility-tracking-north-central-and-north-west-nigeria-june-2024"),
    (REG_NC, 15, 1192416, 200974, None, None, "https://dtm.iom.int/reports/nigeria-north-central-and-north-west-displacement-report-round-15-december-2024"),
    (REG_NC, 16, 1322766, 225458, None, None, "https://dtm.iom.int/reports/nigeria-north-central-and-north-west-round-16-idp-atlas-february-2025"),
    (REG_NC, 17, 1252042, 216288, None, None, "https://dtm.iom.int/data-product-series/site-assessment-2"),
    (REG_NC, 18, 1378124, None, 428969, None, "https://dtm.iom.int/sites/g/files/tmzbdl1461/files/reports/IDP%20and%20Returnee%20Atlas%20NCNW%20R18%20-%20Oct%202025.pdf"),
]


def official_comparison():
    """The 'ALL ROUNDS COMPARISM' sheet of the NE file holds the published round totals."""
    out = []
    for f in sorted(RAW.glob("ne_mobility_*.xlsx")):
        x = pd.read_excel(f, sheet_name=None)
        for name, df in x.items():
            if norm(name).startswith("all rounds compar"):
                for r in df.itertuples(index=False):
                    rn = rnum(r[0])
                    if rn:
                        dt = pd.to_datetime(r[1], errors="coerce")
                        out.append({"region": REG_NE, "round": rn, "idp_ind": I(r[2]), "ret_ind": I(r[3]) or None,
                                    "date": dt.strftime("%Y-%m") if not pd.isna(dt) else None, "source_file": f.name})
    return out


# Places assessed as stated in dataset descriptions on HDX (used to cross-check the distinct counts)
REPORTED_COUNTS = [
    (REG_NC, 8, {"idp_wards": 861, "idp_lgas": 178}, "https://data.humdata.org/dataset/nigeria-displacement-data-north-central-west-site-assessment-iom-dtm"),
    (REG_NC, 9, {"idp_wards": 859, "idp_lgas": 177}, "https://data.humdata.org/dataset/nigeria-displacement-data-north-central-west-site-assessment-iom-dtm"),
    (REG_NC, 10, {"idp_wards": 856, "idp_lgas": 174, "idp_locations": 1690}, "https://data.humdata.org/dataset/nigeria-displacement-data-north-central-west-site-assessment-iom-dtm"),
    (REG_NC, 11, {"idp_wards": 881, "idp_lgas": 180, "idp_locations": 1758, "hc_locations": 1652, "camps": 106}, "https://data.humdata.org/dataset/nigeria-displacement-data-north-central-west-site-assessment-iom-dtm"),
    (REG_NC, 10, {"camp_ind": 217205, "hc_ind": 870670}, "https://dtm.iom.int/reports/nigeria-north-central-and-north-west-mobility-tracking-round-10-idp-atlas-october-2022"),
    (REG_NC, 12, {"idp_wards": 826}, "https://dtm.iom.int/nigeria"),
    (REG_NC, 13, {"idp_locations": 1646, "camp_ind": 196502, "hc_ind": 895694}, "https://dtm.iom.int/reports/nigeria-north-central-and-north-west-round-13-idp-atlas-march-2024"),
    (REG_NC, 14, {"idp_locations": 1733, "camp_ind": 301437, "hc_ind": 1001006}, "https://dtm.iom.int/data-product-series/site-assessment-2"),
    (REG_NC, 15, {"camp_ind": 218863, "hc_ind": 973553}, "https://dtm.iom.int/reports/nigeria-north-central-and-north-west-displacement-report-round-15-december-2024"),
    (REG_NC, 15, {"idp_locations": 1690, "idp_wards": 854, "idp_lgas": 187}, "https://dtm.iom.int/reports/nigeria-north-central-and-north-west-displacement-report-round-15-december-2024"),
    (REG_NC, 16, {"idp_locations": 1761, "camps": 104, "hc_locations": 1657, "camp_ind": 239862, "hc_ind": 1082904}, "https://data.humdata.org/dataset/nigeria-displacement-data-north-central-west-site-assessment-iom-dtm"),
    (REG_NE, 18, {"camps": 235}, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 19, {"camps": 242}, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 20, {"camps": 251}, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 41, {"idp_locations": 2365, "camps": 290, "hc_locations": 2075}, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 44, {"idp_locations": 2482, "camp_ind": 834836, "hc_ind": 1553867}, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 24, {"camp_ind": 753761, "camp_hh": 153049}, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 51, {"camp_ind": 912881, "hc_ind": 1300127, "ret_idp_ind": 2036044, "ret_abroad_ind": 217260}, "https://dtm.iom.int/product-series/displacement-report-15"),
    (REG_NE, 40, {"ret_idp_ind": 1802160, "ret_abroad_ind": 158398}, "https://dtm.iom.int/product-series/returnee-dashboard"),
    (REG_NC, 17, {"camp_ind": 212281, "hc_ind": 959079}, "https://dtm.iom.int/fr/nigeria"),
    (REG_NE, 45, {"camp_ind": 921201, "hc_ind": 1374333, "ret_idp_ind": 1866796, "ret_abroad_ind": 208461}, "https://dtm.iom.int/data-product-series/site-assessment-2"),
    (REG_NE, 46, {"idp_locations": 2333, "camps": 273, "hc_locations": 2060}, "https://reliefweb.int/report/nigeria/north-east-nigeria-mobility-tracking-round-46-idp-and-returnee-atlas-december-2023"),
    (REG_NE, 39, {"idp_locations": 2381, "camps": 309}, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
    (REG_NE, 40, {"idp_locations": 2371, "camps": 299, "hc_locations": 2072}, "https://data.humdata.org/dataset/nigeria-site-assessment-data"),
]

# State figures quoted in reports (report text or cited by UNHCR/EUAA from DTM)
REPORTED_STATES = [
    (REG_NE, 2, {"Borno": {"idp_ind": 672714}, "Adamawa": {"idp_ind": 220159}}, "https://dtm.iom.int/dtm_download_track/471?file=1&type=node&id=576"),
    (REG_NE, 49, {"Borno": {"idp_ind": 1704175}, "Adamawa": {"idp_ind": 200211}}, "https://www.euaa.europa.eu/print/pdf/node/28927"),
    (REG_NE, 45, {"Borno": {"idp_hh": 370446}}, "https://dtm.iom.int/sites/g/files/tmzbdl1461/files/reports/DTM%20Nigeria%20-%20Household%20Intention%20Survey%20report%20April%202024%20-%20Borno%20State.pdf"),
    (REG_NC, 16, {"Benue": {"idp_ind": 457666}, "Katsina": {"idp_ind": 270968}, "Zamfara": {"idp_ind": 216968}}, "https://www.euaa.europa.eu/print/pdf/node/28927"),
]

ROUND_KEYS = ["idp_ind", "idp_hh", "ret_ind", "ret_hh", "idp_locations", "camps", "hc_locations", "ret_locations"]


def load_round_figures():
    rows = list(REPORTED)
    f = RAW / "round_figures.csv"
    if f.exists():
        for r in pd.read_csv(f).to_dict("records"):
            r = {k: (None if pd.isna(v) else v) for k, v in r.items()}
            r["round"] = int(r["round"])
            r["region"] = REG_NE if "east" in str(r.get("region", "")).lower() else REG_NC
            for k in ROUND_KEYS:
                if r.get(k) is not None:
                    r[k] = int(r[k])
            if r.get("date"):
                r["date"] = str(r["date"])[:7]
            rows.append(r)
            print(f"  round_figures.csv: {r['region']} R{r['round']}")
    return rows


def build_region(prefix, region, reported):
    reference = {}
    for rep in reported:
        if rep["region"] == region:
            if rep.get("idp_ind"):
                reference[(rep["round"], False)] = rep["idp_ind"]
            if rep.get("ret_ind"):
                reference[(rep["round"], True)] = rep["ret_ind"]
    for reg, rn, a_, b_, c_, d_, url in REPORT_CHECKS:
        if reg == region:
            if a_:
                reference.setdefault((rn, False), a_)
            if c_:
                reference.setdefault((rn, True), c_)
    lga_rows, meta, reasons, notes, places = load_mobility(prefix, reference)
    # cross-check figures: summary sheet of the Excel file, then published reports
    for oc in official_comparison() if region == REG_NE else []:
        m = meta.get(oc["round"])
        if m and not m.get("date") and oc.get("date"):
            m["date"] = oc["date"]            # the IDP sheet has no publication month for the newest rounds
        if m:
            rep = m.setdefault("reported", {})
            rep.setdefault("idp_ind", oc["idp_ind"])
            if oc["ret_ind"]:
                rep.setdefault("ret_ind", oc["ret_ind"])
            m.setdefault("reported_src", "published round totals (comparison sheet in " + oc["source_file"] + ")")
    for reg, rn, a, b, c, d, url in REPORT_CHECKS:
        if reg != region or rn not in meta:
            continue
        rep = meta[rn].setdefault("reported", {})
        for k, v in zip(("idp_ind", "idp_hh", "ret_ind", "ret_hh"), (a, b, c, d)):
            if v is not None:
                rep[k] = v            # a published report outranks the summary sheet
        meta[rn]["reported_src"] = "published report"
        meta[rn].setdefault("links", []).append(url)
    for rep in [r for r in reported if r["region"] == region]:
        m = meta.setdefault(rep["round"], {"round": rep["round"], "source": "report"})
        for k in ROUND_KEYS + ["date", "note", "reasons_pct"]:
            if rep.get(k) is not None and m.get(k) is None:
                m[k] = rep[k]
        if m.get("source") == "dataset":
            for k in ["idp_ind", "idp_hh", "ret_ind", "ret_hh"]:
                if rep.get(k) is not None:
                    m.setdefault("reported", {})[k] = rep[k]
        if rep.get("source"):
            m.setdefault("links", []).append(rep["source"])
        for u in rep.get("extra_links", []):
            m.setdefault("links", []).append(u)
    for reg, rn, sts, url in REPORTED_STATES:
        if reg == region and rn in meta:
            meta[rn].setdefault("states_reported", {}).update(sts)
            meta[rn].setdefault("links", []).append(url)
    for reg, rn, vals, url in REPORTED_COUNTS:
        if reg == region:
            m = meta.setdefault(rn, {"round": rn, "source": "report"})
            for k, v in vals.items():
                m.setdefault("reported", {})[k] = v
                if k in ("idp_locations", "hc_locations", "camps", "camp_ind", "hc_ind", "camp_hh") and m.get(k) is None:
                    m[k] = v
                    m.setdefault("from_report", []).append(k)
            m.setdefault("links", []).append(url)
    rounds = sorted(meta.values(), key=lambda r: r["round"])
    for r in rounds:
        r["year"] = int(r["date"][:4]) if r.get("date") else None
    return {"rounds": rounds, "lga_rounds": packed(lga_rows, LGA_COLS),
            "reasons": packed(sorted(reasons, key=lambda x: (x["round"], x["state"])), REASON_COLS),
            "places": packed(places, PLACE_COLS)}


# ================================================================= ETT
TRIG = {"POOR LIVING CONDITION": "Poor living conditions", "FEAR OF ATTACK": "Fear of attack", "FAIR OF ATTACK": "Fear of attack",
        "INSECURITY": "Fear of attack", "ATTACK": "Attack / conflict", "CONFLICT/ATTACK": "Attack / conflict",
        "CONFLICT ATTACK": "Attack / conflict", "ATTACK BY NSAG": "Attack / conflict", "INSURGENCY": "Attack / conflict",
        "MILITARY OPERATION": "Military operations", "SEASONAL FARMING": "Seasonal farming", "FARMING": "Seasonal farming",
        "FARMING PURPOSE": "Seasonal farming", "SCOUTING FOR FARMLAND": "Seasonal farming",
        "IMPROVED SECURITY": "Improved security", "VOLUNTARY RELOCATION": "Voluntary relocation",
        "FAMILY REUNIFICATION": "Family reunification", "REUNIT WITH FAMILY MEMBER": "Family reunification",
        "FLOOD": "Flood", "HERDER/FARMER CLASH": "Farmer–herder clash", "COMMUNAL CLASH": "Communal clash",
        "ACCESS TO HUMANITARIAN SUPPORT": "Access to humanitarian support", "BETTER LIVING CONDITION": "Better living conditions",
        "CAMP CLOSER": "Camp closure", "CAMP CLOSURE": "Camp closure", "INVOLUTARY RELOCATION": "Involuntary relocation",
        "INVOLUNTARY RELOCATION": "Involuntary relocation", "GOVERNMENT RE-INTAERATION": "Government re-integration",
        "ARMED BANDITRY AND KIDNAPPING": "Armed banditry & kidnapping"}


def norm_trigger(v):
    v = re.sub(r"\s+", " ", str(v).strip().upper())
    if v in ("NAN", "", "NONE"):
        return "Not stated"
    v = v.replace("RE-UNIFICATION", "REUNIFICATION").rstrip("S")
    return TRIG.get(v, v.capitalize())


def norm_pop(v):
    v = str(v).strip().upper()
    return "Returnees" if v.startswith("RETURN") else "Farmers" if v.startswith("FARM") else "Newly displaced" if "NEW" in v else "IDPs"


ETT_SYN = {"date": ["date of movement", "movement date", "date"], "state": ["state"], "lga": ["lga"], "ward": ["ward"],
           "location": ["location"], "pop": ["idps", "population type", "population group"], "trigger": ["trigger"],
           "mtype": ["movment type", "movement type"], "ind": ["ind", "individuals"], "hh": ["hh", "households"]}
ETT_LGA_COLS = ["p", "state", "lga", "ind", "hh", "records", "locations"]


def build_ett():
    frames = []
    for f in sorted(list(RAW.glob("ett_*.xlsx")) + list(RAW.glob("ett_*.csv"))):
        for sheet, df in read_any(f).items():
            cols = {norm(c): c for c in df.columns}
            cm = {k: next((cols[n] for n in v if n in cols), None) for k, v in ETT_SYN.items()}
            if not cm["date"] or not cm["state"] or not cm["ind"]:
                continue
            e = pd.DataFrame({"date": pd.to_datetime(df[cm["date"]], errors="coerce"), "state": df[cm["state"]].map(tstate),
                              "lga": df[cm["lga"]].map(tlga) if cm["lga"] else "Unknown",
                              "loc": (df[cm["ward"]].astype(str) + "|" + df[cm["location"]].astype(str)).str.upper() if cm["location"] else "",
                              "pop": df[cm["pop"]].map(norm_pop) if cm["pop"] else "IDPs",
                              "trigger": df[cm["trigger"]].map(norm_trigger) if cm["trigger"] else "Not stated",
                              "mtype": df[cm["mtype"]].astype(str).str.strip().str.title() if cm["mtype"] else "Not stated",
                              "ind": pd.to_numeric(df[cm["ind"]], errors="coerce").fillna(0),
                              "hh": pd.to_numeric(df[cm["hh"]], errors="coerce").fillna(0) if cm["hh"] else 0})
            frames.append(e)
            print(f"  {f.name}:{sheet} -> {len(e)} ETT records")
    e = pd.concat(frames, ignore_index=True).dropna(subset=["date"])
    e["loc"] = e["lga"].str.upper() + "|" + e["loc"]
    iso = e["date"].dt.isocalendar()
    e["week"] = iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    e["month"] = e["date"].dt.strftime("%Y-%m")
    e["year"] = e["date"].dt.strftime("%Y")

    def lga_table(pcol):
        g = e.groupby([pcol, "state", "lga"]).agg(ind=("ind", "sum"), hh=("hh", "sum"), records=("ind", "size"),
                                                  locations=("loc", "nunique")).reset_index()
        return packed([{"p": a[0], "state": a[1], "lga": a[2], "ind": int(a[3]), "hh": int(a[4]), "records": int(a[5]),
                        "locations": int(a[6])} for a in g.itertuples(index=False)], ETT_LGA_COLS)

    def agg(keys):
        g = e.groupby(keys).agg(ind=("ind", "sum"), hh=("hh", "sum"), records=("ind", "size")).reset_index()
        return [{**dict(zip(keys, row[:len(keys)])), "ind": int(row[-3]), "hh": int(row[-2]), "records": int(row[-1])}
                for row in g.itertuples(index=False)]

    daily = e.groupby([e.date.dt.strftime("%Y-%m-%d"), "state"]).ind.sum().reset_index()
    return {"first_date": e.date.min().strftime("%Y-%m-%d"), "last_date": e.date.max().strftime("%Y-%m-%d"),
            "lga_week": lga_table("week"), "lga_month": lga_table("month"), "lga_year": lga_table("year"),
            "monthly": agg(["month", "state", "pop"]), "triggers": agg(["month", "state", "trigger"]),
            "daily": [{"d": a[0], "s": a[1], "i": int(a[2])} for a in daily.itertuples(index=False)]}


# ================================================================= incidents dataset (optional)
INC_SYN = {"date": ["date of incident", "incident date", "date"], "state": ["state"], "lga": ["lga"],
           "type": ["incident type", "type of incident", "incident", "type"],
           "ind": ["displaced individuals", "individuals displaced", "ind", "individuals"],
           "hh": ["displaced households", "households displaced", "hh", "households"],
           "deaths": ["deaths", "fatalities", "number of deaths", "killed"],
           "injured": ["injured", "injuries", "number of injured"],
           "locations": ["communities affected", "locations affected", "number of locations"]}


def build_incidents():
    rows = []
    for f in sorted(list(RAW.glob("incidents_*.xlsx")) + list(RAW.glob("incidents_*.csv"))):
        for sheet, df in read_any(f).items():
            cols = {norm(c): c for c in df.columns}
            cm = {k: next((cols[n] for n in v if n in cols), None) for k, v in INC_SYN.items()}
            if not cm["date"] or not cm["state"]:
                continue
            for _, r in df.iterrows():
                dt = pd.to_datetime(r[cm["date"]], errors="coerce")
                if pd.isna(dt):
                    continue
                num = lambda k: I(pd.to_numeric(r[cm[k]], errors="coerce")) if cm[k] else None
                rows.append({"d": dt.strftime("%Y-%m-%d"), "state": tstate(r[cm["state"]]),
                             "lga": tlga(r[cm["lga"]]) if cm["lga"] else None,
                             "type": str(r[cm["type"]]) if cm["type"] else None, "ind": num("ind"), "hh": num("hh"),
                             "deaths": num("deaths"), "injured": num("injured"), "locations": num("locations")})
            print(f"  {f.name}:{sheet} -> {len(rows)} incident rows")
    return rows


# ================================================================= floods
def build_flood():
    frames = []
    for f in sorted(list(RAW.glob("flood_*.xlsx")) + list(RAW.glob("flood_*.csv"))):
        for _, df in read_any(f).items():
            if "State" in df.columns and "Affected Individuals" in df.columns:
                frames.append(df)
    f = pd.concat(frames, ignore_index=True)
    f["state"] = f["State"].map(tstate)
    f["lga"] = f["LGA"].map(tlga)
    ycol = next(c for c in f.columns if norm(c).startswith("year"))
    f["year"] = pd.to_numeric(f[ycol], errors="coerce").astype("Int64")
    num = ["Affected Households", "Affected Individuals", "Displaced Households", "Displaced Individuals",
           "Number of Injured", "Number of Deaths", "shelters completely destroyed?", "shelters partially damaged but need repairs?"]
    for c in num:
        f[c] = pd.to_numeric(f[c], errors="coerce").fillna(0)
    spec = dict(aff_hh=("Affected Households", "sum"), aff_ind=("Affected Individuals", "sum"),
                dis_hh=("Displaced Households", "sum"), dis_ind=("Displaced Individuals", "sum"),
                injured=("Number of Injured", "sum"), deaths=("Number of Deaths", "sum"),
                communities=("Community Name", "size"), sh_destroyed=("shelters completely destroyed?", "sum"),
                sh_partial=("shelters partially damaged but need repairs?", "sum"))
    keys = list(spec)
    g = f.groupby(["year", "state", "lga"]).agg(**spec).reset_index()
    lga = [{"year": int(a.year), "state": a.state, "lga": a.lga, "region": region_of_state(a.state),
            "zone": SUB_REGION.get(a.state, "Other"), **{k: int(getattr(a, k)) for k in keys}} for a in g.itertuples(index=False)]
    needs_cols = ["Shelter", "Food", "NFI", "Psychosocial Support", "Health", "Education", "Security",
                  "Transport", "Water/Sanitation", "Cash/vouchers", "Search and Rescue"]
    needs = f.groupby("year")[needs_cols].sum().reset_index()
    return {"lga_year": lga, "needs": [{"year": int(r["year"]), **{c: int(r[c]) for c in needs_cols}} for _, r in needs.iterrows()]}


# Biometric registration figures from DTM Nigeria reports (publication order). The harvester adds new reports.
def _lg(pairs):
    return [{"lga": n, "hh": h, "ind": i} for n, h, i in pairs]


BIOMETRIC = [
    # ---- North East: Borno, Adamawa, Yobe
    {"state": "Borno", "date": "2017-02", "hh": None, "ind": 340734,
     "lgas": [{"lga": "Maiduguri", "hh": None, "ind": 114294}, {"lga": "Jere", "hh": None, "ind": 100412}, {"lga": "Monguno", "hh": None, "ind": 48461}],
     "note": "North East total in this report: 141,247 households, 505,431 individuals (Borno, Adamawa, Yobe)",
     "title": "DTM Nigeria – Biometric Registration Report, North East (February 2017)",
     "url": "https://dtm.iom.int/system/tdf/reports/IOM%20Nigeria%20DTM%20Biometric%20Report%20-%20February%202017.pdf?file=1&type=node&id=665"},
    {"state": "Adamawa", "date": "2017-02", "hh": None, "ind": 143496, "lgas": [{"lga": "Hong", "hh": None, "ind": 53280}],
     "title": "DTM Nigeria – Biometric Registration Report, North East (February 2017)",
     "url": "https://dtm.iom.int/system/tdf/reports/IOM%20Nigeria%20DTM%20Biometric%20Report%20-%20February%202017.pdf?file=1&type=node&id=665"},
    {"state": "Yobe", "date": "2017-02", "hh": None, "ind": 21201, "lgas": [],
     "title": "DTM Nigeria – Biometric Registration Report, North East (February 2017)",
     "url": "https://dtm.iom.int/system/tdf/reports/IOM%20Nigeria%20DTM%20Biometric%20Report%20-%20February%202017.pdf?file=1&type=node&id=665"},
    {"state": "Borno", "date": "2018-01-13", "hh": 2124, "ind": 9235, "locations": 1, "lgas": _lg([("Custom House Camp (Jere)", 2124, 9235)]),
     "title": "Borno State – Custom House Camp – Biometric Registration Update (13 January 2018)",
     "url": "https://reliefweb.int/report/nigeria/nigeria-borno-state-custom-house-camp-biometric-registration-update-13-january-2018"},
    {"state": "Borno", "date": "2022-03-21", "hh": 5799, "ind": 28251, "locations": 1, "lgas": _lg([("Muna El Badawy Camp (Jere)", 5799, 28251)]),
     "title": "Biometric Registration Report – Muna El Badawy Camp, Borno State (21 March 2022)",
     "url": "https://dtm.iom.int/reports/nigeria-biometric-registration-report-muna-el-badawy-camp-borno-state-21-march-2022"},
    {"state": "Adamawa", "date": "2023-06-16", "hh": 188, "ind": 1105, "locations": 1, "lgas": _lg([("Malkohi New City", 188, 1105)]),
     "title": "Biometric Registration Update – Malkohi New City, Adamawa State (16 June 2023)",
     "url": "https://dtm.iom.int/product-series/biometric-registration-update"},
    {"state": "Adamawa", "date": "2023-08-30", "hh": 797, "ind": 5390, "locations": 4, "vulnerable": 534,
     "lgas": _lg([("Daware camp", 375, 2004), ("Labondo", 323, 1788), ("Salama housing camp", 99, 491), ("Malkohi new city", None, 1107)]),
     "title": "Biometric Registration Report Adamawa State – IDPs on the Solutions Pathway (20 September 2023)",
     "url": "https://dtm.iom.int/reports/nigeria-biometric-registration-report-adamawa-state-idps-solutions-pathway-20-september"},
    # ---- Benue
    {"state": "Benue", "date": "2023-12-01", "hh": 23095, "ind": 88785, "vulnerable": 10191,
     "lgas": _lg([("Guma", 6750, 23283), ("Agatu", 5649, 21895), ("Kwande", 3232, 15934), ("Makurdi", 4150, 14041), ("Logo", 3314, 13632)]),
     "title": "Biometric Registration Report Benue State (1 December 2023)",
     "url": "https://dtm.iom.int/sites/g/files/tmzbdl1461/files/reports/Biometric%20Registration%20Report%20Benue%20State_01%20December%202023_Final.pdf"},
    {"state": "Benue", "date": "2024-01-01", "hh": 29275, "ind": 111751,
     "lgas": _lg([("Guma", 9513, 33586), ("Agatu", 5641, 21861), ("Kwande", 3225, 15909), ("Makurdi", 4150, 14041), ("Logo", 3311, 13633), ("Gwer West", 3435, 12721)]),
     "title": "Biometric Registration Report Benue State (1 January 2024)",
     "url": "https://dtm.iom.int/sites/g/files/tmzbdl1461/files/reports/Biometric%20Registration%20Report%20Benue%20State_01%20January%202024_Final.pdf"},
    {"state": "Benue", "date": "2024-07-01", "hh": 42663, "ind": 160631, "vulnerable": 20044,
     "lgas": _lg([("Guma", 17636, 60445), ("Makurdi", 9415, 36062), ("Agatu", 5641, 21861), ("Kwande", 3225, 15909), ("Logo", 3311, 13633), ("Gwer West", 3435, 12721)]),
     "title": "Biometric Registration Update Report 4 – Benue State (1 July 2024)",
     "url": "https://dtm.iom.int/reports/nigeria-biometric-registration-update-report-4-benue-state-1-july-2024"},
    {"state": "Benue", "date": None, "hh": 48163, "ind": 184711, "locations": 65, "vulnerable": 22868,
     "lgas": _lg([("Guma", 20239, 72601), ("Makurdi", 12314, 47990), ("Agatu", 5641, 21861), ("Kwande", 3225, 15909), ("Logo", 3311, 13633), ("Gwer West", 3433, 12717)]),
     "title": "Biometric Registration Update – Benue State (update after Report 4, 65 locations)",
     "url": "https://dtm.iom.int/product-series/biometric-registration-update"},
    {"state": "Benue", "date": None, "hh": 57464, "ind": 212860, "locations": 98, "vulnerable": 28030,
     "lgas": [{"lga": n, "hh": None, "ind": v} for n, v in [("Guma", 76418), ("Makurdi", 53915), ("Agatu", 21835), ("Kwande", 15909), ("Logo", 13629),
              ("Gwer West", 12588), ("Apa", 5977), ("Gwer East", 5520), ("Konshisha", 2212), ("Ado", 1551), ("Katsina-Ala", 1287),
              ("Buruku", 1061), ("Tarka", 683), ("Oju", 275)]],
     "note": "14 LGAs, 98 locations; 56% female, 44% male",
     "title": "Biometric Registration Update – Benue State (212,860 individuals, 98 locations, late 2025)",
     "url": "https://dtm.iom.int/component/registration"},
    {"state": "Benue", "date": None, "hh": 59268, "ind": 219477, "lgas": [],
     "note": "Latest figure on dtm.iom.int; LGA breakdown to be read by the harvester from the report",
     "title": "Biometric Registration Update – Benue State (latest: 219,477 individuals)",
     "url": "https://dtm.iom.int/component/biometric-registration"},
]


if __name__ == "__main__":
    rep = load_round_figures()
    print("North East:")
    ne = build_region("ne_mobility_", REG_NE, rep)
    print("North Central & North West:")
    nc = build_region("nwnc_mobility_", REG_NC, rep)
    print("ETT / incidents / floods:")
    out = {"reason_order": REASON_ORDER, "north_east": ne, "nc_nw": nc, "ett": build_ett(), "incidents": build_incidents(), "flood": build_flood(),
           "expected_rounds": {REG_NE: 65, REG_NC: 35}, "biometric": BIOMETRIC}
    OUT.write_text(json.dumps(out, separators=(",", ":"), default=str))
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
