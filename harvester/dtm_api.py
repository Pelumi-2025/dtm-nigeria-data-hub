"""
Pulls Nigeria IDP figures from the official DTM public API (https://dtm.iom.int/data-and-analysis/dtm-api).

The API gives IDP individuals per round at state (admin1) and LGA (admin2) level,
with the reporting date, operation (North East / North Central-North West) and,
where DTM records it, the reason for displacement. build_site.py uses it to
cross-check the Excel files and to fill rounds that have no Excel file yet.

Needs a free subscription key: register at https://dtm-apim-portal.iom.int/ and
store it as the DTM_API_KEY secret (GitHub) or environment variable.

  python dtm_api.py
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "harvest" / "dtm_api.json"
BASES = ["https://dtmapi.iom.int/v3/displacement", "https://dtmapi.iom.int/v2/IdpAdmin{lvl}Data/GetAdmin{lvl}Datav2"]


def pick(d, *keys):
    for k in keys:
        for kk in (k, k[0].upper() + k[1:]):
            if kk in d and d[kk] not in (None, ""):
                return d[kk]
    return None


def fetch(level, key):
    headers = {"Ocp-Apim-Subscription-Key": key, "User-Agent": "DTM-Nigeria-Data-Hub/1.0"}
    urls = [f"{BASES[0]}/admin{level}?CountryName=Nigeria", BASES[1].format(lvl=level) + "?CountryName=Nigeria"]
    for url in urls:
        for attempt in range(3):
            try:
                r = requests.get(url, headers=headers, timeout=120)
                if r.status_code == 200:
                    js = r.json()
                    rows = js.get("result") if isinstance(js, dict) else js
                    if rows:
                        print(f"  admin{level}: {len(rows)} rows from {url}", file=sys.stderr)
                        return rows
                    break
                print(f"  HTTP {r.status_code} {url}", file=sys.stderr)
                if r.status_code in (401, 403, 404):
                    break
            except (requests.RequestException, ValueError) as e:
                print(f"  retry {attempt + 1}: {e}", file=sys.stderr)
            time.sleep(5)
    return []


def norm(row, level):
    op = str(pick(row, "operation") or "").lower()
    region = ("North Central & North West" if ("central" in op or "west" in op) else
              "North East" if ("east" in op or "lake chad" in op or not op) else "Other")
    date = str(pick(row, "reportingDate") or "")[:10]
    return {"region": region, "operation": pick(row, "operation"), "round": pick(row, "roundNumber"),
            "date": date or None, "state": (pick(row, "admin1Name") or "").title(),
            "lga": (pick(row, "admin2Name") or "").title() if level == 2 else None,
            "idp_ind": pick(row, "numPresentIdpInd", "numberPresentIdpInd", "numPresentIdp"),
            "reason": pick(row, "displacementReason", "reasonForDisplacement"),
            "assessment": pick(row, "assessmentType")}


def main():
    key = os.environ.get("DTM_API_KEY")
    if not key:
        print("DTM API skipped (set DTM_API_KEY to enable)", file=sys.stderr)
        return
    out = {"admin1": [norm(r, 1) for r in fetch(1, key)], "admin2": [norm(r, 2) for r in fetch(2, key)]}
    if out["admin1"] or out["admin2"]:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(out))
        print(f"saved {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
