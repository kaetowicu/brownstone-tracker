"""Turn raw downloads into one analysis-ready DuckDB database.

Tables written to data/tracker.duckdb:
  sales_all     every Brooklyn sale record, tidied
  lots          PLUTO lots + area assignment + depth inside historic district
  districts     historic district names, designation dates, assigned area
  dob_jobs      renovation / new building / demolition filings, both systems
  sales         sales in your target + comparison areas, joined to lots, with
                all the cleaning flags (one row per sale)
"""

from __future__ import annotations

import json

import duckdb
import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import unary_union

from . import config as C
from .sales import load_all_sales

TO_FEET = Transformer.from_crs("EPSG:4326", "EPSG:2263", always_xy=True)  # NY Long Island ft


def _num(s: pd.Series) -> pd.Series:
    if s is None:
        return None
    return pd.to_numeric(
        s.astype("string").str.replace(r"[$,\s]", "", regex=True), errors="coerce"
    ).astype("float64")


# ---------------------------------------------------------------------------
# Historic districts
# ---------------------------------------------------------------------------

def _area_for_district() -> dict:
    return {d.lower(): area for area, spec in C.ALL_AREAS.items() for d in spec["districts"]}


def load_districts():
    """Return (districts table, union geometry in feet)."""
    gj = json.loads((C.RAW_DIR / "historic_districts.geojson").read_text())
    rows, geoms = [], []
    area_map = _area_for_district()
    for f in gj["features"]:
        p = f.get("properties", {}) or {}
        if f.get("geometry") is None:
            continue
        name = p.get("area_name") or p.get("name") or ""
        status = (p.get("status_of_") or p.get("last_actio") or "").upper()
        if status and "DESIGNATED" not in status:
            continue  # calendared / heard but not designated: no protection yet
        g = shape(f["geometry"])
        # NYC's file says lon/lat (CRS84) but actually ships State Plane feet.
        # Only reproject coordinates that really are degrees.
        if max(abs(v) for v in g.bounds) <= 180:
            g = shapely.transform(
                g, lambda xy: np.column_stack(TO_FEET.transform(xy[:, 0], xy[:, 1])))
        geoms.append(shapely.make_valid(g))
        rows.append({
            "district": name,
            "borough": p.get("borough"),
            "designated": pd.to_datetime(p.get("desdate"), errors="coerce"),
            "area": area_map.get(name.lower()),
        })
    union = unary_union(geoms) if geoms else None
    return pd.DataFrame(rows).drop_duplicates("district"), union


# ---------------------------------------------------------------------------
# Lots
# ---------------------------------------------------------------------------

def load_lots(union) -> pd.DataFrame:
    lots = pd.read_parquet(C.RAW_DIR / "pluto_bk.parquet")
    num_cols = ["bbl", "lotarea", "bldgarea", "resarea", "numbldgs", "numfloors",
                "unitsres", "unitstotal", "lotfront", "lotdepth", "bldgfront",
                "bldgdepth", "proxcode", "yearbuilt", "yearalter1", "yearalter2",
                "builtfar", "residfar", "xcoord", "ycoord", "latitude", "longitude"]
    for c in num_cols:
        if c in lots.columns:
            lots[c] = _num(lots[c])
    lots = lots.dropna(subset=["bbl"])
    lots["bbl"] = lots["bbl"].astype("int64")

    # Coordinates in feet: PLUTO x/y are already NY State Plane feet.
    x, y = lots.get("xcoord"), lots.get("ycoord")
    missing = x.isna() | y.isna()
    if missing.any() and {"longitude", "latitude"} <= set(lots.columns):
        fx, fy = TO_FEET.transform(lots.loc[missing, "longitude"].to_numpy(),
                                   lots.loc[missing, "latitude"].to_numpy())
        lots.loc[missing, "xcoord"], lots.loc[missing, "ycoord"] = fx, fy

    # Signed depth: + feet inside the nearest district edge, - feet outside.
    lots["district_depth_ft"] = np.nan
    has = lots["xcoord"].notna() & lots["ycoord"].notna()
    if union is not None and has.any():
        pts = shapely.points(lots.loc[has, "xcoord"].to_numpy(), lots.loc[has, "ycoord"].to_numpy())
        boundary = union.boundary
        shapely.prepare(union)
        shapely.prepare(boundary)
        dist = shapely.distance(pts, boundary)
        inside = shapely.contains(union, pts)
        lots.loc[has, "district_depth_ft"] = np.where(inside, dist, -dist).round(0)

    lots["histdist"] = lots.get("histdist").astype("string").str.strip()
    lots["in_district"] = lots["histdist"].notna() & (lots["histdist"] != "")

    # Sanity check: PLUTO's district flag and our geometry should agree.
    flagged = lots.loc[lots["in_district"] & lots["district_depth_ft"].notna(), "district_depth_ft"]
    if len(flagged):
        agree = (flagged > 0).mean()
        print(f"  {agree:.0%} of lots PLUTO places in a district are inside the boundary geometry"
              f" (median depth {flagged.median():,.0f} ft)")
        if agree < 0.8:
            print("  WARNING: district geometry and PLUTO disagree; depth values are unreliable")
    area_map = _area_for_district()
    lots["district_area"] = lots["histdist"].str.lower().map(area_map)

    # Unused development rights: allowed residential FAR minus built FAR.
    lots["unused_far"] = (lots["residfar"] - lots["builtfar"]).clip(lower=0)
    lots["width_ft"] = lots["bldgfront"].where(lots["bldgfront"] > 0, lots["lotfront"])
    return lots


# ---------------------------------------------------------------------------
# DOB filings -> one tidy table
# ---------------------------------------------------------------------------

def load_dob() -> pd.DataFrame:
    frames = []
    p = C.RAW_DIR / "dob_bis_jobs.parquet"
    if p.exists():
        b = pd.read_parquet(p)
        frames.append(pd.DataFrame({
            "system": "BIS",
            "job_id": b["job__"].astype("string"),
            "bbl": 3_000_000_000 + _num(b["block"]) * 10_000 + _num(b["lot"]),
            "job_type": b["job_type"].astype("string"),
            "filed": pd.to_datetime(b["pre__filing_date"], format="%m/%d/%Y", errors="coerce"),
            "permitted": pd.to_datetime(b.get("fully_permitted"), format="%m/%d/%Y", errors="coerce"),
            "cost": _num(b["initial_cost"]),
            "units_before": _num(b.get("existing_dwelling_units")),
            "units_after": _num(b.get("proposed_dwelling_units")),
            "owner_business": b.get("owner_s_business_name"),
        }))
    p = C.RAW_DIR / "dob_now_jobs.parquet"
    if p.exists():
        n = pd.read_parquet(p)
        bbl = _num(n["bbl"]) if "bbl" in n else None
        fallback = 3_000_000_000 + _num(n["block"]) * 10_000 + _num(n["lot"])
        jt = n["job_type"].astype("string").map({
            "Alteration CO": "A1", "Alteration": "A2",
            "New Building": "NB", "Full Demolition": "DM"}).fillna(n["job_type"])
        frames.append(pd.DataFrame({
            "system": "DOB NOW",
            "job_id": n["job_filing_number"].astype("string"),
            "bbl": bbl.fillna(fallback) if bbl is not None else fallback,
            "job_type": jt.astype("string"),
            "filed": pd.to_datetime(n["filing_date"], errors="coerce"),
            "permitted": pd.to_datetime(n.get("first_permit_date"), errors="coerce"),
            "cost": _num(n["initial_cost"]),
            "units_before": _num(n.get("existing_dwelling_units")),
            "units_after": _num(n.get("proposed_dwelling_units")),
            "owner_business": n.get("owner_s_business_name"),
        }))
    if not frames:
        return pd.DataFrame(columns=["system", "job_id", "bbl", "job_type", "filed",
                                     "permitted", "cost", "units_before", "units_after",
                                     "owner_business", "major_reno"])
    d = pd.concat(frames, ignore_index=True).dropna(subset=["bbl", "filed"])
    d["bbl"] = d["bbl"].astype("int64")
    d["major_reno"] = (d["job_type"] == "A1") | (
        d["job_type"].isin(["A1", "A2"]) & (d["cost"] >= C.MAJOR_RENO_MIN_COST))
    return d


def _reno_flags(sales: pd.DataFrame, dob: pd.DataFrame) -> pd.DataFrame:
    """For each sale: was there a major renovation shortly before (sold
    renovated) or shortly after (sold needing work)?"""
    out = sales[["sale_id", "bbl", "sale_date"]].copy()
    reno = dob.loc[dob["major_reno"], ["bbl", "filed", "cost"]]
    if reno.empty:
        out["reno_before_sale"] = False
        out["reno_after_sale"] = False
        out["reno_after_cost"] = np.nan
        return out.drop(columns=["bbl", "sale_date"])
    m = out.merge(reno, on="bbl", how="left")
    before = (m["filed"] < m["sale_date"]) & (
        m["filed"] >= m["sale_date"] - pd.DateOffset(years=C.RENO_BEFORE_SALE_YEARS))
    after = (m["filed"] >= m["sale_date"]) & (
        m["filed"] <= m["sale_date"] + pd.DateOffset(months=C.RENO_AFTER_SALE_MONTHS))
    m["b"], m["a"] = before, after
    m["a_cost"] = m["cost"].where(after)
    g = m.groupby("sale_id").agg(reno_before_sale=("b", "any"),
                                 reno_after_sale=("a", "any"),
                                 reno_after_cost=("a_cost", "max"))
    return g.reset_index()


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------

def _family(cls: pd.Series) -> pd.Series:
    cls = cls.fillna("")
    fam = pd.Series(pd.NA, index=cls.index, dtype="Int64")
    fam[cls.str.startswith(C.ONE_FAMILY_CLASSES)] = 1
    fam[cls.str.startswith(C.TWO_FAMILY_CLASSES)] = 2
    fam[cls.str.startswith(C.THREE_FAMILY_CLASSES)] = 3
    return fam


def build_sales(sales_all: pd.DataFrame, lots: pd.DataFrame, dob: pd.DataFrame) -> pd.DataFrame:
    dof_area = {n: area for area, spec in C.ALL_AREAS.items() for n in spec["dof"]}
    s = sales_all.copy()
    s["dof_area"] = s["neighborhood"].map(dof_area)

    lot_cols = ["bbl", "histdist", "in_district", "district_area", "district_depth_ft",
                "width_ft", "lotfront", "lotdepth", "bldgdepth", "numfloors", "bldgarea",
                "resarea", "unitsres", "proxcode", "yearbuilt", "yearalter1", "yearalter2",
                "builtfar", "residfar", "unused_far", "zonedist1", "landmark",
                "latitude", "longitude", "xcoord", "ycoord", "ownername"]
    lot_cols = [c for c in lot_cols if c in lots.columns]
    s = s.merge(lots[lot_cols], on="bbl", how="left", validate="many_to_one")

    # Area: district membership wins over the DOF label.
    s["area"] = s["district_area"].fillna(s["dof_area"])
    s = s[s["area"].notna()].copy()
    s["is_target"] = s["area"].isin(C.TARGET_AREAS.keys())
    s["in_district"] = s["in_district"].fillna(False).astype(bool)
    s["sale_id"] = np.arange(len(s))

    # --- product type -------------------------------------------------------
    s["family"] = _family(s["bldg_class_at_sale"])
    s["is_1to3_family"] = s["family"].notna()

    # --- market-sale flags --------------------------------------------------
    s["below_min_price"] = s["sale_price"].fillna(0) < C.MIN_MARKET_PRICE
    # Package deals: one price recorded against several lots on the same day
    # (usually adjacent lots, so same tax block).
    key = ["sale_date", "sale_price", "block"]
    s["lots_in_deed"] = s.groupby(key)["bbl"].transform("nunique")
    s["is_package"] = (s["lots_in_deed"] > 1) & ~s["below_min_price"]

    sqft = s["gross_sqft"].where(s["gross_sqft"] > 0, s["bldgarea"])
    s["sqft"] = sqft.where(sqft > 0)
    s["price_per_sqft"] = s["sale_price"] / s["sqft"]
    ok = ~s["below_min_price"] & ~s["is_package"] & s["is_1to3_family"]
    med = (s[ok].groupby(["area", "sale_year"])["price_per_sqft"].median()
           .rename("area_year_median_ppsf"))
    s = s.merge(med, on=["area", "sale_year"], how="left")
    s["low_outlier"] = s["price_per_sqft"] < C.LOW_OUTLIER_FRACTION * s["area_year_median_ppsf"]
    s["low_outlier"] = s["low_outlier"].fillna(False)
    s["is_market_sale"] = ~s["below_min_price"] & ~s["is_package"] & ~s["low_outlier"]

    # --- brownstone-type product -------------------------------------------
    s["is_brownstone_type"] = (
        s["is_1to3_family"]
        & s["yearbuilt"].between(1800, C.BROWNSTONE_MAX_YEAR_BUILT)
        & s["numfloors"].between(C.BROWNSTONE_MIN_FLOORS, C.BROWNSTONE_MAX_FLOORS)
        & s["width_ft"].between(C.BROWNSTONE_MIN_WIDTH_FT, C.BROWNSTONE_MAX_WIDTH_FT)
        & s["proxcode"].isin(C.BROWNSTONE_PROXCODES)
    ).fillna(False)

    s["in_budget"] = s["sale_price"].between(C.BUDGET_MIN, C.BUDGET_MAX)
    s["is_interior"] = (s["district_depth_ft"] >= C.INTERIOR_MIN_FT).fillna(False)

    # --- history of this lot -----------------------------------------------
    s = s.sort_values(["bbl", "sale_date"])
    mkt = s["is_market_sale"]
    prev = s[mkt].groupby("bbl")[["sale_date", "sale_price"]].shift(1)
    s.loc[mkt, "prev_sale_date"] = prev["sale_date"]
    s.loc[mkt, "prev_sale_price"] = prev["sale_price"]
    s["is_repeat_sale"] = s["prev_sale_date"].notna()
    s["months_since_prev"] = ((s["sale_date"] - s["prev_sale_date"]).dt.days / 30.44).round(1)
    s["is_flip"] = s["months_since_prev"] < 24

    # --- renovation context from DOB ----------------------------------------
    s = s.merge(_reno_flags(s, dob), on="sale_id", how="left")
    for c in ["reno_before_sale", "reno_after_sale"]:
        s[c] = s[c].fillna(False).astype(bool)

    s["owner_is_llc_now"] = s.get("ownername", pd.Series(index=s.index, dtype="string")) \
        .astype("string").str.contains(r"\bLLC\b|\bL\.L\.C\b|\bINC\b|\bCORP\b|TRUST", regex=True) \
        .fillna(False)
    return s.sort_values(["area", "sale_date"]).reset_index(drop=True)


# ---------------------------------------------------------------------------

def build() -> None:
    print("Reading sales files")
    sales_all = load_all_sales()
    print(f"  {len(sales_all):,} Brooklyn sale records, "
          f"{sales_all['sale_date'].min():%Y-%m} to {sales_all['sale_date'].max():%Y-%m}")
    print("Reading historic districts")
    districts, union = load_districts()
    print(f"  {len(districts)} designated districts")
    print("Reading PLUTO lots and computing district depth")
    lots = load_lots(union)
    print("Reading DOB filings")
    dob = load_dob()
    print(f"  {len(dob):,} filings, {int(dob['major_reno'].sum()):,} major renovations")
    print("Building analysis table")
    sales = build_sales(sales_all, lots, dob)
    print(f"  {len(sales):,} sale records in your target + comparison areas")

    C.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(C.DB_PATH))
    for name, df in [("sales_all", sales_all), ("lots", lots), ("districts", districts),
                     ("dob_jobs", dob), ("sales", sales)]:
        con.register("tmp_df", df)
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM tmp_df")
        con.unregister("tmp_df")
    con.close()
    print(f"Wrote {C.DB_PATH}")
