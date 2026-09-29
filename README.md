# DTM Nigeria Data Hub

Scrapes everything IOM DTM Nigeria publishes (dtm.iom.int/nigeria, dtm.iom.int/datasets filtered to Nigeria, response.reliefweb.int/nigeria/data, HDX data.humdata.org,
the ReliefWeb API, HDX and the DTM public API), cross-checks it against the curated data files, and
publishes a live dashboard that also works offline.

## What is in this folder
| Path | What it is |
|---|---|
| `data/raw/online/` | Data files downloaded from the websites (filled automatically). For each round these are used first; the attached files in `data/raw/` fill any round that is not online or whose online totals disagree by more than 5% |
| `data/raw/` | Attached data files. Add new ones with these name prefixes: `ne_mobility_*`, `nwnc_mobility_*`, `ett_*`, `incidents_*`, `flood_*`, plus optional `round_figures.csv` |
| `data/reports/` | Drop report PDFs here; the harvester reads them on its next run |
| `data/harvest/` | Scraped results (filled automatically – do not edit) |
| `harvester/config.py` | Keywords for the 12 components, regions and states |
| `harvester/build_baseline.py` | Reads the data files (any number of rounds) |
| `harvester/harvest_web.py` | Crawls the websites, reads PDFs, extracts figures, LGAs, communities, reasons for displacement |
| `harvester/dtm_api.py` | DTM public API (needs a free key) |
| `harvester/harvest_datasets.py` | Downloads the Mobility Tracking Excel/CSV files published on dtm.iom.int and HDX into `data/raw/online/` |
| `harvester/build_site.py` | Merges everything, cross-checks, builds `dist/` |
| `site/` | Dashboard page, offline service worker, app manifest |
| `dist/` | Built dashboard – `dist/index.html` works offline on its own |
| `.github/workflows/harvest.yml` | Runs everything on GitHub every 6 hours and publishes to GitHub Pages |

## Run on your own computer (optional)
```
pip install -r requirements.txt
cd harvester
python build_baseline.py
python dtm_api.py            # only if DTM_API_KEY is set
python harvest_web.py        # full crawl: 1–3 hours the first time; later runs are incremental
python build_site.py         # then open dist/index.html
```
Quick test: `python harvest_web.py --max-pages 3 --no-pdf`

See GITHUB_UPLOAD_GUIDE.md for publishing.

## Reasons for displacement
Codes in the DTM files are harmonised to: 1 Insurgency, 2 Communal Clashes, 3 Farmers-Herders Clashes,
4 Natural/Climate-related Disaster, and Armed Banditry/Kidnapping (banditry, kidnapping and their spellings combined).

## Components shown
Mobility Tracking (IDP & Returnee Atlas, Needs Monitoring), Incident/Flash Reports, Emergency Tracking Tool,
Post-Flood Situation Reports, Biometric Registration and other DTM products. Intention Surveys, Socio-Economic Surveys,
Solutions Mobility / Stability Index, Transhumance Flow Monitoring, EWER dashboards and SES & Facility Mapping are
recognised by the harvester but left out of the dashboard (`EXCLUDED_COMPONENTS` in `harvester/config.py`).

## Ask the data (AI chat)
The chat box sends the question to Claude together with a set of query tools over the dashboard data
(`TOOLS` in `site/index.html`). Claude calls the tools, reads the exact figures and answers with households
before individuals, the round/period, the source (report or data file) and links. On claude.ai it uses the
page's built-in Claude access; on GitHub Pages each viewer adds their own Anthropic API key under
**AI settings** (stored in the browser only). Without either it falls back to offline rule-based answers.
