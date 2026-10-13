"""Offline end-to-end test: builds fake raw files in the formats the city
publishes, runs build + report, and checks the cleaning logic.

    python -m pytest tests/        or        python tests/test_pipeline.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import build, config as C, report  # noqa: E402

RNG = np.random.default_rng(7)

# A square "historic district" around central Park Slope, in lon/lat.
PS_LON, PS_LAT = -73.9760, 40.6720
HALF = 0.004  # ~1,100 ft east-west


def _sales_rows(year: int, n: int, lots: pd.DataFrame) -> list[dict]:
    rows = []
    for _ in range(n):
        lot = lots.sample(1, random_state=int(RNG.integers(1e9))).iloc[0]
        fam = int(lot["unitsres"])
        cls = {1: "A4", 2: "B1", 3: "C0"}[fam]
        price = float(lot["bldgarea"]) * RNG.normal(900, 120) * (1.04 ** (year - 2008))
        rows.append({
            "BOROUGH": 3,
            "NEIGHBORHOOD": lot["_dof"],
            "CAT": f"0{fam}  {'ONE' if fam == 1 else 'TWO' if fam == 2 else 'THREE'} FAMILY HOMES",
            "BLOCK": int(lot["_block"]), "LOT": int(lot["_lot"]),
            "ADDRESS": lot["address"], "UNITS": fam,
            "GSF": f"{int(lot['bldgarea']):,}", "YB": 1899,
            "CLS": cls, "PRICE": round(price, -3),
            "DATE": pd.Timestamp(year, int(RNG.integers(1, 13)), int(RNG.integers(1, 28))),
        })
    # Non-market transfer and a two-lot package deal
    first = rows[0]
    rows.append({**first, "PRICE": 0})
    rows.append({**first, "PRICE": 10})
    pk = {**rows[1], "PRICE": 4_000_000, "DATE": pd.Timestamp(year, 6, 15)}
    rows.append(pk)
    rows.append({**pk, "LOT": pk["LOT"] + 1})
    return rows


def _write_xls_old(path: Path, rows: list[dict]) -> None:
    import xlwt
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Brooklyn")
    ws.write(0, 0, "Brooklyn Sales for 2008")
    ws.write(1, 0, "All Sales From January 2008 - December 2008")
    header = ["BOROUGH", "NEIGHBORHOOD", "BUILDING CLASS CATEGORY", "TAX CLASS AT PRESENT",
              "BLOCK", "LOT", "EASE-MENT", "BUILDING CLASS AT PRESENT", "ADDRESS",
              "APART\nMENT\nNUMBER", "ZIP CODE", "RESIDENTIAL UNITS", "COMMERCIAL UNITS",
              "TOTAL UNITS", "LAND SQUARE FEET", "GROSS SQUARE FEET", "YEAR BUILT",
              "TAX CLASS AT TIME OF SALE", "BUILDING CLASS AT TIME OF SALE",
              "SALE\nPRICE", "SALE DATE"]
    for j, h in enumerate(header):
        ws.write(3, j, h)
    datefmt = xlwt.easyxf(num_format_str="MM/DD/YYYY")
    for i, r in enumerate(rows, start=4):
        vals = [r["BOROUGH"], r["NEIGHBORHOOD"], r["CAT"], "1", r["BLOCK"], r["LOT"], "",
                r["CLS"], r["ADDRESS"], "", 11215, r["UNITS"], 0, r["UNITS"], "2,000",
                r["GSF"], r["YB"], "1", r["CLS"], r["PRICE"]]
        for j, v in enumerate(vals):
            ws.write(i, j, v)
        ws.write(i, 20, r["DATE"].to_pydatetime(), datefmt)
    wb.save(str(path))


def _write_xlsx_new(path: Path, rows: list[dict]) -> None:
    header = ["BOROUGH", "NEIGHBORHOOD", "BUILDING CLASS CATEGORY",
              "TAX CLASS AS OF FINAL ROLL", "BLOCK", "LOT", "EASEMENT",
              "BUILDING CLASS AS OF FINAL ROLL", "ADDRESS", "APARTMENT NUMBER", "ZIP CODE",
              "RESIDENTIAL UNITS", "COMMERCIAL UNITS", "TOTAL UNITS", "LAND SQUARE FEET",
              "GROSS SQUARE FEET", "YEAR BUILT", "TAX CLASS AT TIME OF SALE",
              "BUILDING CLASS AT TIME OF SALE", "SALE PRICE", "SALE DATE"]
    data = [[r["BOROUGH"], r["NEIGHBORHOOD"], r["CAT"].replace("HOMES", "DWELLINGS"), "1",
             r["BLOCK"], r["LOT"], None, r["CLS"], r["ADDRESS"], None, 11215, r["UNITS"], 0,
             r["UNITS"], 2000, int(r["GSF"].replace(",", "")), r["YB"], 1, r["CLS"],
             r["PRICE"], r["DATE"]] for r in rows]
    title = pd.DataFrame([["Brooklyn Annualized Sales"] + [None] * 20,
                          [None] * 21, [None] * 21, [None] * 21])
    body = pd.DataFrame([header] + data)
    pd.concat([title, body]).to_excel(path, header=False, index=False)


def _make_fixtures(raw: Path) -> None:
    (raw / "sales").mkdir(parents=True)

    # Lots: Park Slope (half inside the district), Fort Greene (outside).
    lots = []
    for i in range(120):
        ps = i < 80
        lon = PS_LON + RNG.uniform(-2.2, 2.2) * HALF if ps else -73.9740 + RNG.uniform(-0.003, 0.003)
        lat = PS_LAT + RNG.uniform(-1, 1) * HALF if ps else 40.6890 + RNG.uniform(-0.003, 0.003)
        inside = ps and abs(lon - PS_LON) < HALF and abs(lat - PS_LAT) < HALF
        fam = int(RNG.choice([1, 2, 2, 3]))
        block, lot = 1000 + i // 10, i % 10 * 3 + 1
        lots.append({
            "bbl": f"{3_000_000_000 + block * 10_000 + lot}.00000000",
            "address": f"{100 + i} TEST STREET", "bldgclass": {1: "A4", 2: "B1", 3: "C0"}[fam],
            "lotarea": "2000", "bldgarea": str(int(RNG.integers(2800, 4600))),
            "resarea": "3500", "numbldgs": "1", "numfloors": "4", "unitsres": str(fam),
            "unitstotal": str(fam), "lotfront": str(RNG.choice([16.67, 18.75, 20.0, 25.0])),
            "lotdepth": "100", "bldgfront": "0", "bldgdepth": "45",
            "proxcode": "3" if i % 17 else "1", "yearbuilt": "1899" if i % 13 else "1965",
            "yearalter1": "0", "yearalter2": "0",
            "histdist": "Park Slope Historic District" if inside else None,
            "landmark": None, "builtfar": "1.8", "residfar": "2.0", "zonedist1": "R6B",
            "ownername": "TEST LLC" if i % 5 == 0 else "SMITH, JANE",
            "xcoord": None, "ycoord": None,  # force the lon/lat fallback path
            "latitude": str(lat), "longitude": str(lon),
            "_dof": "PARK SLOPE" if ps else "FORT GREENE", "_block": block, "_lot": lot,
        })
    lots = pd.DataFrame(lots)
    lots.drop(columns=["_dof", "_block", "_lot"]).to_parquet(raw / "pluto_bk.parquet")
    # Package-deal partner lots need to exist too (lot+1); fine if missing in PLUTO.

    _write_xls_old(raw / "sales" / "brooklyn_2008.xls", _sales_rows(2008, 60, lots))
    _write_xlsx_new(raw / "sales" / "brooklyn_2020.xlsx", _sales_rows(2020, 60, lots))
    # Rolling file duplicates part of 2020; dedupe must drop the overlap.
    dup = _sales_rows(2020, 5, lots)
    _write_xlsx_new(raw / "sales" / "brooklyn_rolling.xlsx", dup)
    _write_xlsx_new(raw / "sales" / "brooklyn_2019.xlsx", dup)

    # District polygon
    ring = [[PS_LON - HALF, PS_LAT - HALF], [PS_LON + HALF, PS_LAT - HALF],
            [PS_LON + HALF, PS_LAT + HALF], [PS_LON - HALF, PS_LAT + HALF],
            [PS_LON - HALF, PS_LAT - HALF]]
    # Like the real file: labeled CRS84 but coordinates in State Plane feet.
    ring_ft = [list(build.TO_FEET.transform(x, y)) for x, y in ring]
    gj = {"type": "FeatureCollection",
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
          "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring_ft]},
         "properties": {"area_name": "Park Slope Historic District", "borough": "BK",
                        "status_of_": "DESIGNATED", "desdate": "1973-07-17T00:00:00.000"}},
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
         "properties": {"area_name": "Proposed Thing", "status_of_": "CALENDARED"}},
    ]}
    (raw / "historic_districts.geojson").write_text(json.dumps(gj))

    # DOB: a major reno on one lot in 2019 (before a 2020 sale) and one in 2009
    l0 = lots.iloc[0]
    pd.DataFrame([{
        "job__": "320000001", "job_type": "A1", "job_status": "R",
        "block": f"{l0['_block']:05d}", "lot": f"{l0['_lot']:05d}", "bin__": "1",
        "pre__filing_date": "03/01/2019", "approved": None, "fully_permitted": "06/01/2019",
        "initial_cost": "$450000.00", "building_type": "1-2-3 FAMILY",
        "existing_dwelling_units": "2", "proposed_dwelling_units": "1",
        "existingno_of_stories": "4", "proposed_no_of_stories": "4", "landmarked": "Y",
        "owner_type": "INDIVIDUAL", "owner_s_business_name": None,
    }]).to_parquet(raw / "dob_bis_jobs.parquet")
    pd.DataFrame([{
        "job_filing_number": "B00000001-I1", "job_type": "Alteration", "filing_status": "LOC Issued",
        "block": str(l0["_block"]), "lot": str(l0["_lot"]), "bbl": None,
        "filing_date": "2009-02-01T00:00:00.000", "first_permit_date": None,
        "initial_cost": "300000", "building_type": "1 Family",
        "existing_dwelling_units": "1", "proposed_dwelling_units": "1",
        "owner_type": "Individual", "owner_s_business_name": None,
    }]).to_parquet(raw / "dob_now_jobs.parquet")


def test_pipeline():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        C.ROOT, C.DATA_DIR = root, root / "data"
        C.RAW_DIR, C.DB_PATH = root / "data" / "raw", root / "data" / "tracker.duckdb"
        C.REPORT_DIR = root / "reports"
        _make_fixtures(C.RAW_DIR)

        build.build()
        report.run(recent_years=1)

        import duckdb
        con = duckdb.connect(str(C.DB_PATH), read_only=True)
        s = con.execute("SELECT * FROM sales").df()
        d = con.execute("SELECT * FROM districts").df()
        con.close()

        # Both file formats parsed, overlap deduplicated
        assert set(s["sale_year"]) >= {2008, 2020}
        assert s.duplicated(["bbl", "sale_date", "sale_price"]).sum() == 0
        # Nominal transfers and package deals excluded from market sales
        assert not s.loc[s["sale_price"] < 100_000, "is_market_sale"].any()
        assert s["is_package"].sum() >= 4
        assert not s.loc[s["is_package"], "is_market_sale"].any()
        # Only designated districts kept
        assert list(d["district"]) == ["Park Slope Historic District"]
        # District depth: positive inside, negative outside
        inside = s[s["in_district"]]
        assert len(inside) > 0 and (inside["district_depth_ft"] > 0).all()
        # Depths are plausible (district is ~2,200 ft wide, so max depth ~1,100 ft)
        assert inside["district_depth_ft"].max() < 1_300
        assert s["is_interior"].any()
        fg = s.loc[s["area"] == "Fort Greene", "district_depth_ft"].dropna()
        assert len(fg) > 0 and (fg < 0).all()
        # Detached / 1960s lots are not brownstone-type
        assert not s.loc[s["proxcode"] == 1, "is_brownstone_type"].any()
        assert not s.loc[s["yearbuilt"] > 1931, "is_brownstone_type"].any()
        # Renovation flags picked up from both DOB systems
        assert s["reno_before_sale"].any() or s["reno_after_sale"].any()
        # Reports written
        for f in ["funnel_by_area.csv", "funnel_per_year_recent.csv",
                  "repeat_sales_by_area.csv", "descriptive_medians.csv"]:
            assert (C.REPORT_DIR / f).exists(), f
        print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    test_pipeline()
