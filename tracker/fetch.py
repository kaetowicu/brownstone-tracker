"""Download raw data into data/raw/. Files already present are skipped unless
refresh=True, so re-running is cheap and you can also drop files in by hand
(e.g. if nyc.gov blocks a download, save the file from your browser to the
same name)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd
import requests

from . import config as C

HEADERS = {
    # nyc.gov rejects requests without a browser-like user agent.
    "User-Agent": "Mozilla/5.0 (brownstone-tracker; personal research)",
}
APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN")  # optional, raises rate limits


def _get(url: str, params: dict | None = None, retries: int = 4, timeout: int = 120):
    headers = dict(HEADERS)
    if APP_TOKEN and "cityofnewyork" in url:
        headers["X-App-Token"] = APP_TOKEN
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code in (400, 404):
                break  # not worth retrying
        except requests.RequestException as e:
            last = str(e)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} failed: {last}")


def socrata_all(dataset: str, where: str | None = None, select: str | None = None,
                page: int = 50_000) -> pd.DataFrame:
    """Page through a Socrata dataset and return it as a DataFrame."""
    url = f"{C.SOCRATA}/{dataset}.json"
    frames, offset = [], 0
    while True:
        params = {"$limit": page, "$offset": offset, "$order": ":id"}
        if where:
            params["$where"] = where
        if select:
            params["$select"] = select
        rows = _get(url, params).json()
        if not rows:
            break
        frames.append(pd.DataFrame(rows))
        offset += len(rows)
        print(f"    {dataset}: {offset:,} rows", end="\r", flush=True)
        if len(rows) < page:
            break
    print()
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _save(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    print(f"  saved {len(df):,} rows -> {path}")


# ---------------------------------------------------------------------------


def fetch_sales(refresh: bool = False) -> None:
    """DOF Brooklyn sales: one file per year 2003-2025, plus the rolling
    last-12-months file. Saved as-is; parsing happens in sales.py."""
    out = C.RAW_DIR / "sales"
    out.mkdir(parents=True, exist_ok=True)
    failed = []
    for key, url in C.DOF_SALES_FILES.items():
        ext = Path(url).suffix
        dest = out / f"brooklyn_{key}{ext}"
        if dest.exists() and not refresh and key != "rolling":
            continue
        try:
            r = _get(url)
            dest.write_bytes(r.content)
            print(f"  sales {key}: {len(r.content)/1e6:.1f} MB")
        except RuntimeError as e:
            print(f"  sales {key}: FAILED ({e})")
            failed.append(key)

    # Fallback for 2016+ years from the Open Data copy of the same records.
    fallback_years = [k for k in failed if isinstance(k, int) and k >= 2016]
    if fallback_years:
        print(f"  using Open Data fallback for {fallback_years}")
        lo, hi = min(fallback_years), max(fallback_years)
        df = socrata_all(
            C.DATASETS["annualized_sales"],
            where=(f"borough='3' AND sale_date >= '{lo}-01-01' "
                   f"AND sale_date < '{hi + 1}-01-01'"),
        )
        _save(df, out / "opendata_fallback.parquet")
    elif failed:
        print("  Some files failed. Download them in a browser from\n"
              "  https://www.nyc.gov/site/finance/property/property-annualized-sales-update.page\n"
              f"  and save into {out} as brooklyn_<year>.xls(x)")


PLUTO_COLUMNS = [
    "bbl", "address", "zipcode", "bldgclass", "landuse", "ownername", "ownertype",
    "lotarea", "bldgarea", "resarea", "numbldgs", "numfloors", "unitsres",
    "unitstotal", "lotfront", "lotdepth", "bldgfront", "bldgdepth", "proxcode",
    "irrlotcode", "lottype", "bsmtcode", "yearbuilt", "yearalter1", "yearalter2",
    "histdist", "landmark", "builtfar", "residfar", "zonedist1", "overlay1",
    "spdist1", "cd", "xcoord", "ycoord", "latitude", "longitude", "version",
]


def fetch_pluto(refresh: bool = False) -> None:
    dest = C.RAW_DIR / "pluto_bk.parquet"
    if dest.exists() and not refresh:
        return
    print("  PLUTO (Brooklyn, ~280k lots)")
    try:
        df = socrata_all(C.DATASETS["pluto"], where="borough='BK'",
                         select=",".join(PLUTO_COLUMNS))
    except RuntimeError:
        # A renamed column makes $select fail; take everything instead.
        df = socrata_all(C.DATASETS["pluto"], where="borough='BK'")
    _save(df, dest)


def fetch_districts(refresh: bool = False) -> None:
    dest = C.RAW_DIR / "historic_districts.geojson"
    if dest.exists() and not refresh:
        return
    url = f"{C.SOCRATA}/{C.DATASETS['historic_districts']}.geojson"
    r = _get(url, params={"$limit": 5000})
    gj = r.json()
    dest.write_text(json.dumps(gj))
    print(f"  historic districts: {len(gj.get('features', []))} polygons")


def fetch_dob(refresh: bool = False) -> None:
    """Alteration / new building / demolition filings in the relevant
    community boards. BIS covers ~1989-2020ish, DOB NOW covers ~2017+."""
    boards = ",".join(f"'{b}'" for b in C.COMMUNITY_BOARDS)

    dest = C.RAW_DIR / "dob_bis_jobs.parquet"
    if refresh or not dest.exists():
        print("  DOB BIS job filings")
        df = socrata_all(
            C.DATASETS["dob_bis_jobs"],
            where=(f"borough='BROOKLYN' AND community___board in({boards}) "
                   "AND job_type in('A1','A2','NB','DM') AND doc__='01'"),
            select=("job__,job_type,job_status,block,lot,bin__,pre__filing_date,"
                    "approved,fully_permitted,initial_cost,building_type,"
                    "existing_dwelling_units,proposed_dwelling_units,"
                    "existingno_of_stories,proposed_no_of_stories,landmarked,"
                    "owner_type,owner_s_business_name"),
        )
        _save(df, dest)

    dest = C.RAW_DIR / "dob_now_jobs.parquet"
    if refresh or not dest.exists():
        print("  DOB NOW job filings")
        df = socrata_all(
            C.DATASETS["dob_now_jobs"],
            where=(f"borough='Brooklyn' AND commmunity_board in({boards}) "
                   "AND job_type in('Alteration','Alteration CO',"
                   "'New Building','Full Demolition')"),
            select=("job_filing_number,job_type,filing_status,block,lot,bbl,"
                    "filing_date,first_permit_date,initial_cost,building_type,"
                    "existing_dwelling_units,proposed_dwelling_units,"
                    "owner_type,owner_s_business_name"),
        )
        _save(df, dest)


def fetch_all(refresh: bool = False) -> None:
    print("Downloading sales files")
    fetch_sales(refresh)
    print("Downloading PLUTO")
    fetch_pluto(refresh)
    print("Downloading historic district boundaries")
    fetch_districts(refresh)
    print("Downloading DOB filings")
    fetch_dob(refresh)
