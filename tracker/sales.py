"""Parse the DOF sales spreadsheets into one tidy table.

The files changed format several times since 2003: different header row
positions, different column names ("SALE\\nPRICE", "TAX CLASS AT PRESENT" vs
"TAX CLASS AS OF FINAL ROLL 18/19"), different category labels. We locate the
header row, then map columns by keyword rather than exact name. Building class
letter codes (A5, B1, C0...) have been stable throughout, so filters use those.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from . import config as C

# (target column, all keywords that must appear, keywords that must not)
COLUMN_RULES = [
    ("sale_price", ["SALE", "PRICE"], []),
    ("sale_date", ["SALE", "DATE"], []),
    ("bldg_class_category", ["BUILDING CLASS CATEGORY"], []),
    ("bldg_class_at_sale", ["BUILDING CLASS", "TIME OF SALE"], []),
    ("tax_class_at_sale", ["TAX CLASS", "TIME OF SALE"], []),
    ("bldg_class_present", ["BUILDING CLASS"], ["CATEGORY", "TIME OF SALE"]),
    ("tax_class_present", ["TAX CLASS"], ["TIME OF SALE"]),
    ("neighborhood", ["NEIGHBORHOOD"], []),
    ("borough", ["BOROUGH"], []),
    ("block", ["BLOCK"], []),
    ("lot", ["LOT"], []),
    ("easement", ["EASE"], []),
    ("address", ["ADDRESS"], []),
    ("apartment", ["APART"], []),
    ("zip_code", ["ZIP"], []),
    ("residential_units", ["RESIDENTIAL UNITS"], []),
    ("commercial_units", ["COMMERCIAL UNITS"], []),
    ("total_units", ["TOTAL UNITS"], []),
    ("land_sqft", ["LAND SQUARE"], []),
    ("gross_sqft", ["GROSS SQUARE"], []),
    ("year_built", ["YEAR BUILT"], []),
]

KEEP = [r[0] for r in COLUMN_RULES]


def _norm(s) -> str:
    s = str(s).upper().replace("\n", " ").replace("-", " ").replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace("APART MENT", "APARTMENT").replace("EASE MENT", "EASEMENT")


def _map_columns(cols) -> dict:
    mapping, used = {}, set()
    for target, need, avoid in COLUMN_RULES:
        for c in cols:
            n = _norm(c)
            if c in mapping or target in used:
                continue
            if all(k in n for k in need) and not any(k in n for k in avoid):
                mapping[c] = target
                used.add(target)
    return mapping


def _find_header(raw: pd.DataFrame) -> int:
    for i in range(min(25, len(raw))):
        cells = [_norm(v) for v in raw.iloc[i].tolist()]
        if any(c == "BOROUGH" for c in cells) and any("SALE" in c and "PRICE" in c for c in cells):
            return i
    raise ValueError("header row not found")


def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(
        s.astype("string").str.replace(r"[$,\s]", "", regex=True).replace({"": None, "-": None}),
        errors="coerce",
    ).astype("float64")


def read_dof_file(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, header=None, dtype=object)
    h = _find_header(raw)
    df = raw.iloc[h + 1:].copy()
    df.columns = raw.iloc[h].tolist()
    df = df.rename(columns=_map_columns(df.columns))
    df = df[[c for c in KEEP if c in df.columns]]
    df["source_file"] = path.name
    return df


def read_opendata_fallback(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df = df.rename(columns={
        "building_class_category": "bldg_class_category",
        "building_class_at_time_of": "bldg_class_at_sale",
        "tax_class_at_time_of_sale": "tax_class_at_sale",
        "building_class_as_of_final": "bldg_class_present",
        "tax_class_as_of_final_roll": "tax_class_present",
        "land_square_feet": "land_sqft",
        "gross_square_feet": "gross_sqft",
    })
    df = df[[c for c in KEEP if c in df.columns]].copy()
    df["source_file"] = path.name
    return df


def load_all_sales(raw_dir: Path | None = None) -> pd.DataFrame:
    raw_dir = raw_dir or (C.RAW_DIR / "sales")
    frames = []
    for p in sorted(raw_dir.glob("brooklyn_*.xls*")):
        try:
            frames.append(read_dof_file(p))
        except Exception as e:  # keep going; report which file is bad
            print(f"  WARNING: could not parse {p.name}: {e}")
    fb = raw_dir / "opendata_fallback.parquet"
    if fb.exists():
        frames.append(read_opendata_fallback(fb))
    if not frames:
        raise SystemExit(f"No sales files in {raw_dir}. Run `python -m tracker fetch` first.")
    df = pd.concat(frames, ignore_index=True)
    return tidy(df)


def tidy(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in ["sale_price", "block", "lot", "residential_units", "commercial_units",
              "total_units", "land_sqft", "gross_sqft", "year_built", "zip_code"]:
        if c in df.columns:
            df[c] = _to_num(df[c])
    df["sale_date"] = pd.to_datetime(df["sale_date"], errors="coerce")
    for c in ["neighborhood", "bldg_class_category", "bldg_class_at_sale",
              "bldg_class_present", "address", "apartment",
              "tax_class_at_sale", "tax_class_present"]:
        if c in df.columns:
            df[c] = df[c].astype("string").str.strip().str.upper()
    df = df.dropna(subset=["block", "lot", "sale_date"])
    df["block"] = df["block"].astype("int64")
    df["lot"] = df["lot"].astype("int64")
    df["bbl"] = 3_000_000_000 + df["block"] * 10_000 + df["lot"]
    df["sale_year"] = df["sale_date"].dt.year
    # Same deed appears in an annual file and the rolling file / fallback.
    df = (df.sort_values("source_file")
            .drop_duplicates(subset=["bbl", "sale_date", "sale_price", "apartment"], keep="first")
            .reset_index(drop=True))
    return df
