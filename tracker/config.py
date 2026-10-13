"""Everything you are likely to tweak lives here: areas, filters, thresholds.

Area definitions use two keys:
  dof        - the Department of Finance neighborhood labels on each sale record
  districts  - LPC historic district names exactly as they appear in PLUTO's
               `histdist` field (and the LPC boundary file's `area_name`)

A sale is assigned to an area if its lot is inside one of the area's historic
districts; otherwise by its DOF neighborhood label.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
# Each location can be overridden with an environment variable, e.g. to keep
# downloads on Google Drive when running in Colab.
RAW_DIR = Path(os.environ.get("TRACKER_RAW_DIR", DATA_DIR / "raw"))
DB_PATH = Path(os.environ.get("TRACKER_DB_PATH", DATA_DIR / "tracker.duckdb"))
REPORT_DIR = Path(os.environ.get("TRACKER_REPORT_DIR", ROOT / "reports"))

# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

# The areas you are actually considering.
TARGET_AREAS = {
    "Brooklyn Heights": {
        "dof": ["BROOKLYN HEIGHTS"],
        "districts": ["Brooklyn Heights Historic District"],
    },
    "Cobble Hill": {
        "dof": ["COBBLE HILL", "COBBLE HILL-WEST"],
        "districts": [
            "Cobble Hill Historic District",
            "Cobble Hill Historic District Extension",
        ],
    },
    "Park Slope": {
        "dof": ["PARK SLOPE", "PARK SLOPE SOUTH"],
        "districts": [
            "Park Slope Historic District",
            "Park Slope Historic District Extension",
            "Park Slope Historic District Extension II",
        ],
    },
    "Fort Greene": {
        "dof": ["FORT GREENE"],
        "districts": [
            "Fort Greene Historic District",
            "Brooklyn Academy of Music Historic District",
        ],
    },
    "Clinton Hill": {
        "dof": ["CLINTON HILL"],
        "districts": ["Clinton Hill Historic District"],
    },
    "Prospect Heights": {
        "dof": ["PROSPECT HEIGHTS"],
        "districts": ["Prospect Heights Historic District"],
    },
    "Carroll Gardens": {
        "dof": ["CARROLL GARDENS"],
        "districts": ["Carroll Gardens Historic District"],
    },
}

# Not targets, but brownstone markets whose sales help the model learn what
# house features (width, renovation, district depth) are worth, and which
# serve as benchmarks for drawdowns.
COMPARISON_AREAS = {
    "Boerum Hill": {
        "dof": ["BOERUM HILL"],
        "districts": [
            "Boerum Hill Historic District",
            "Boerum Hill Historic District Extension",
        ],
    },
    "Bed-Stuy": {
        "dof": ["BEDFORD STUYVESANT"],
        "districts": [
            "Stuyvesant Heights Historic District",
            "Bedford Stuyvesant / Expanded Stuyvesant Heights Historic District",
            "Bedford Historic District",
        ],
    },
    "Crown Heights North": {
        "dof": ["CROWN HEIGHTS"],
        "districts": [
            "Crown Heights North Historic District",
            "Crown Heights North II Historic District",
            "Crown Heights North III Historic District",
        ],
    },
    "Prospect Lefferts Gardens": {
        "dof": ["FLATBUSH-LEFFERTS GARDEN"],
        "districts": ["Prospect Lefferts Gardens Historic District"],
    },
    "Windsor Terrace": {
        "dof": ["WINDSOR TERRACE"],
        "districts": [],
    },
}

ALL_AREAS = {**TARGET_AREAS, **COMPARISON_AREAS}

# Community boards covering these areas; used to limit the DOB permit download.
# 302 Bklyn Hts/Cobble Hill/Boerum Hill/Fort Greene/Clinton Hill
# 303 Bed-Stuy, 306 Park Slope/Carroll Gardens, 307 Windsor Terrace,
# 308 Prospect Heights/Crown Heights North, 309 Lefferts
COMMUNITY_BOARDS = ["302", "303", "306", "307", "308", "309"]

# ---------------------------------------------------------------------------
# Sales cleaning
# ---------------------------------------------------------------------------

# Building classes at time of sale counted as 1-3 family houses.
# A = one family, B = two family, C0 = three family.
ONE_FAMILY_CLASSES = ("A",)
TWO_FAMILY_CLASSES = ("B",)
THREE_FAMILY_CLASSES = ("C0",)

# Below this a recorded "sale" is almost always a transfer (family, estate,
# LLC reshuffle), not a market sale.
MIN_MARKET_PRICE = 100_000

# A sale whose $/sq ft is below this fraction of its area-year median is
# flagged as a probable non-market transfer (e.g. partial-interest sale).
LOW_OUTLIER_FRACTION = 0.35

# "Brownstone-type" house: what we treat as comparable product.
BROWNSTONE_MAX_YEAR_BUILT = 1931   # pre-war rowhouse stock
BROWNSTONE_MIN_FLOORS = 2
BROWNSTONE_MAX_FLOORS = 5
BROWNSTONE_MIN_WIDTH_FT = 11
BROWNSTONE_MAX_WIDTH_FT = 40
# PLUTO proximity code: 1 detached, 2 semi-attached, 3 attached
BROWNSTONE_PROXCODES = (2, 3)

# Your search
BUDGET_MIN = 2_000_000
BUDGET_MAX = 5_000_000

# Lots at least this far (feet, straight line) inside any historic district
# boundary count as "interior". ~250 ft is roughly one short block.
INTERIOR_MIN_FT = 250

# ---------------------------------------------------------------------------
# Renovation signals from DOB filings
# ---------------------------------------------------------------------------

# A filing counts as a major renovation if it is a BIS "A1" / DOB NOW
# "Alteration CO" job, or any alteration with stated cost at or above this.
MAJOR_RENO_MIN_COST = 150_000
# Renovation in this window before a sale => house likely sold renovated.
RENO_BEFORE_SALE_YEARS = 4
# Big filing this soon after a sale => house likely sold needing work.
RENO_AFTER_SALE_MONTHS = 18

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

SOCRATA = "https://data.cityofnewyork.us/resource"
DATASETS = {
    "pluto": "64uk-42ks",
    "historic_districts": "skyk-mpzq",
    "annualized_sales": "w2pb-icbu",   # fallback for 2016+ if a file fails
    "dob_bis_jobs": "ic3t-wcy2",
    "dob_now_jobs": "w9ak-ipjd",
}

_DOF = "https://www.nyc.gov/assets/finance/downloads"
DOF_SALES_FILES = {
    2003: f"{_DOF}/sales_brooklyn_03.xls",
    2004: f"{_DOF}/sales_brooklyn_04.xls",
    2005: f"{_DOF}/sales_brooklyn_05.xls",
    2006: f"{_DOF}/sales_brooklyn_06.xls",
    2007: f"{_DOF}/excel/rolling_sales/sales_2007_brooklyn.xls",
    2008: f"{_DOF}/pdf/09pdf/rolling_sales/sales_2008_brooklyn.xls",
    2009: f"{_DOF}/pdf/rolling_sales/annualized-sales/2009_brooklyn.xls",
    **{
        y: f"{_DOF}/pdf/rolling_sales/annualized-sales/{y}/{y}_brooklyn.xls"
        for y in range(2010, 2018)
    },
    **{
        y: f"{_DOF}/pdf/rolling_sales/annualized-sales/{y}/{y}_brooklyn.xlsx"
        for y in range(2018, 2026)
    },
    # Last 12 months; overlaps the latest annual file, duplicates are dropped.
    "rolling": f"{_DOF}/pdf/rolling_sales/rollingsales_brooklyn.xlsx",
}
