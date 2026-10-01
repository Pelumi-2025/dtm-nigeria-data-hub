"""
Configuration for the DTM Nigeria Data Hub harvester.
Edit the keyword lists here to change how reports are classified.
Matching is case-insensitive and runs against title + summary (+ PDF text when available).
"""

USER_AGENT = (
    "DTM-Nigeria-Data-Hub/1.0 (IOM DTM Nigeria information management; "
    "contact: DTMNigeria@iom.int)"
)

# ---------------------------------------------------------------- sources
DTM_BASE = "https://dtm.iom.int"
DTM_COUNTRY_LISTING = "https://dtm.iom.int/nigeria?page={page}"
DTM_DATASET_LISTING = "https://dtm.iom.int/datasets?f%5B0%5D=dataset_country%3A76&page={page}"   # every Nigeria dataset
# Extra listings walked page by page ("brute force"): DTM product series and ReliefWeb searches
EXTRA_LISTINGS = [
    "https://dtm.iom.int/operations/north-central-and-north-west?page={page}",
    "https://dtm.iom.int/product-series/emergency-tracking-report?page={page}",
    "https://dtm.iom.int/product-series/displacement-report-15?page={page}",
    "https://dtm.iom.int/data-product-series/site-assessment-2?page={page}",
    "https://dtm.iom.int/product-series/biometric-registration-update?page={page}",
    "https://dtm.iom.int/component/biometric-registration?page={page}",
    "https://dtm.iom.int/nigeria?field_component1_target_id_verf=All&field_component2_target_id_verf=All&sort_by=field_published_date_value&sort_order=DESC&page={page}",
    "https://reliefweb.int/updates?search=%22Emergency%20Tracking%20Tool%22%20Nigeria&page={page}",
    "https://reliefweb.int/updates?search=DTM%20Nigeria%20flash%20report&page={page}",
    "https://reliefweb.int/updates?search=DTM%20Nigeria%20mobility%20tracking&page={page}",
    "https://reliefweb.int/updates?search=DTM%20Nigeria%20displacement%20report&page={page}",
    "https://reliefweb.int/updates?search=IOM%20Nigeria%20biometric%20registration&page={page}",
]
DTM_SITEMAP = "https://dtm.iom.int/sitemap.xml"
RELIEFWEB_RESPONSE_LISTING = "https://response.reliefweb.int/nigeria/data?page={page}"
# ReliefWeb API v2: an approved appname is required since 2025
# (request one at https://apidoc.reliefweb.int/parameters#appname and put it in the
# RELIEFWEB_APPNAME secret / environment variable).
RELIEFWEB_API = "https://api.reliefweb.int/v2/reports"
HDX_API = "https://data.humdata.org/api/3/action/package_search"

MAX_LISTING_PAGES = 400          # safety cap for paginated listings
REQUEST_DELAY_SECONDS = 1.5      # be polite to the servers
DOWNLOAD_PDFS = True             # mine figures from PDFs (slower, much richer)
PDF_MAX_PAGES = 6                # figures are almost always on the first pages

# ---------------------------------------------------------------- components
# Order matters: the first component whose keywords match wins.
# "any" = at least one phrase must appear; "none" = phrases that veto the match.
COMPONENTS = [
    {"id": "mt_atlas", "name": "Mobility Tracking – IDP & Returnee Atlas",
     "any": ["idp and returnee atlas", "idp & returnee atlas", "idp atlas", "returnee atlas",
             "mobility tracking round", "baseline assessment", "site assessment",
             "location assessment", "master list", "displacement report", "baseline dashboard",
             "round report"],
     "none": ["needs monitoring", "flash report", "emergency tracking"]},
    {"id": "mt_needs", "name": "Mobility Tracking – Needs Monitoring",
     "any": ["needs monitoring", "multi-sectoral needs", "msna", "needs assessment",
             "displacement and needs"]},
    {"id": "flash", "name": "Incident / Flash Report",
     "any": ["flash report", "incident report", "flash update", "rapid assessment of incident"]},
    {"id": "ett", "name": "Emergency Tracking Tool (ETT)",
     "any": ["emergency tracking tool", "ett report", "ett dashboard", "emergency tracking"]},
    {"id": "intention", "name": "Intention Surveys",
     "any": ["intention survey", "intentions survey", "return intention", "intention of idps",
             "camp closure"]},
    {"id": "ses_fm", "name": "Socio-Economic Survey & Facility Mapping",
     "any": ["facility mapping", "facilities mapping", "socio-economic survey and facility",
             "socio-economic survey & facility"]},
    {"id": "ses", "name": "Socio-Economic Survey",
     "any": ["socio-economic survey", "socio economic survey", "socioeconomic survey",
             "socio-economic profile", "livelihood survey"]},
    {"id": "smi", "name": "Solutions Mobility Index / Stability Index",
     "any": ["solutions mobility index", "solution mobility index", "solutions and mobility index",
             "stability index", "smi "]},
    {"id": "transhumance", "name": "Transhumance Flow Monitoring",
     "any": ["flow monitoring dashboard", "transhumance tracking tool", "transhumance flow",
             "flow monitoring"],
     "none": ["early warning"]},
    {"id": "ewer", "name": "Early Warning & Early Response Dashboard",
     "any": ["early warning dashboard", "early warning", "early response", "ewer", "alert tool"]},
    {"id": "flood", "name": "Post-Flood Situation Report (Ad-Hoc)",
     "any": ["flood", "post-flood", "flooding", "flood impact"]},
    {"id": "biometric", "name": "Biometric Registration Report",
     "any": ["biometric registration", "biometric", "registration report", "bio-registration"]},
]
OTHER_COMPONENT = {"id": "other", "name": "Other DTM products"}

# Components that are recognised (so they are not mistaken for others) but left out of the dashboard.
EXCLUDED_COMPONENTS = {"intention", "ses", "smi", "ses_fm"}

# All 36 states and the FCT, by geopolitical zone
ZONES = {
    "North East": ["Adamawa", "Bauchi", "Borno", "Gombe", "Taraba", "Yobe"],
    "North Central": ["Benue", "FCT", "Kogi", "Kwara", "Nasarawa", "Niger", "Plateau"],
    "North West": ["Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Sokoto", "Zamfara"],
    "South East": ["Abia", "Anambra", "Ebonyi", "Enugu", "Imo"],
    "South South": ["Akwa Ibom", "Bayelsa", "Cross River", "Delta", "Edo", "Rivers"],
    "South West": ["Ekiti", "Lagos", "Ogun", "Ondo", "Osun", "Oyo"],
}
ALL_STATES = [s for z in ZONES.values() for s in z]

# ---------------------------------------------------------------- geography
REGIONS = {
    "North East": ["Adamawa", "Borno", "Yobe", "Gombe", "Taraba", "Bauchi"],
    "North Central & North West": ["Benue", "Nasarawa", "Plateau", "Kogi", "Niger",
                                    "Kano", "Kaduna", "Katsina", "Sokoto", "Zamfara"],
}
SUB_REGION = {
    **{s: "North East" for s in REGIONS["North East"]},
    **{s: "North Central" for s in ["Benue", "Nasarawa", "Plateau", "Kogi", "Niger", "Kwara", "FCT"]},
    **{s: "North West" for s in ["Kano", "Kaduna", "Katsina", "Sokoto", "Zamfara", "Kebbi", "Jigawa"]},
}
TRACKED_STATES = REGIONS["North East"] + REGIONS["North Central & North West"]

REGION_KEYWORDS = {
    "North East": ["north-east", "north east", "northeast", "bay states", "lake chad"],
    "North Central & North West": ["north-central", "north central", "northcentral",
                                    "north-west", "north west", "northwest", "nc/nw", "ncnw"],
}


def region_of_state(state: str) -> str:
    s = (state or "").strip().title()
    if s in REGIONS["North East"]:
        return "North East"
    if s in REGIONS["North Central & North West"] or s in ZONES["North Central"] or s in ZONES["North West"]:
        return "North Central & North West"
    return "Other"
