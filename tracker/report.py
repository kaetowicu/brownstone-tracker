"""The 'how much data do we really have' report.

Applies the filters one at a time so you can see where sales drop out, by
area, overall and per year. This is what decides how much pooling the model
needs and where street-level detail is possible.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from . import config as C

STAGES = [
    ("all_records", "All recorded transfers", None),
    ("one_to_three_family", "1-3 family houses", "is_1to3_family"),
    ("market_sales", "...at market prices", "is_market_sale"),
    ("brownstone_type", "...pre-1932 attached rowhouse", "is_brownstone_type"),
    ("one_or_two_family", "...1 or 2 family", "family <= 2"),
    ("in_district", "...inside a historic district", "in_district"),
    ("in_budget", f"...${C.BUDGET_MIN/1e6:.0f}-{C.BUDGET_MAX/1e6:.0f}M (nominal)", "in_budget"),
    ("interior", f"...{C.INTERIOR_MIN_FT}+ ft inside the edge", "is_interior"),
]


def _funnel(s: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    mask = pd.Series(True, index=s.index)
    cols = {}
    for key, _, cond in STAGES:
        if cond == "family <= 2":
            mask &= (s["family"] <= 2).fillna(False)
        elif cond:
            mask &= s[cond].astype(bool)
        cols[key] = s[mask].groupby(by).size()
    out = pd.DataFrame(cols).fillna(0).astype(int)
    return out


def run(recent_years: int = 5, quiet: bool = False) -> dict:
    """Write the CSVs and return the tables. quiet=True skips printing
    (the Colab notebook displays them as tables instead)."""
    con = duckdb.connect(str(C.DB_PATH), read_only=True)
    s = con.execute("SELECT * FROM sales").df()
    con.close()
    C.REPORT_DIR.mkdir(parents=True, exist_ok=True)

    first, last = int(s["sale_year"].min()), int(s["sale_year"].max())
    n_years = last - first + 1
    order = list(C.TARGET_AREAS) + list(C.COMPARISON_AREAS)

    # Totals over the whole history
    tot = _funnel(s, ["area"]).reindex(order).fillna(0).astype(int)
    tot.to_csv(C.REPORT_DIR / "funnel_by_area.csv")

    # Per year
    by_year = _funnel(s, ["area", "sale_year"])
    by_year.to_csv(C.REPORT_DIR / "funnel_by_area_year.csv")

    # Average per year over recent full years (the rolling file can make the
    # latest year partial, so it is excluded from the average).
    recent = s[(s["sale_year"] > last - 1 - recent_years) & (s["sale_year"] < last)]
    per_year = (_funnel(recent, ["area"]) / recent_years).reindex(order).fillna(0).round(1)
    per_year.to_csv(C.REPORT_DIR / "funnel_per_year_recent.csv")

    # Repeat-sale pairs among comparable market sales (feeds a repeat-sales index)
    comp = s[s["is_market_sale"] & s["is_brownstone_type"]].copy()
    comp["repeat_after_reno"] = comp["is_repeat_sale"] & comp["reno_before_sale"]
    pairs = comp.groupby("area").agg(
        comparable_sales=("sale_id", "size"),
        repeat_pairs=("is_repeat_sale", "sum"),
        repeat_pairs_renovated=("repeat_after_reno", "sum"),
        flips_under_24m=("is_flip", "sum"),
        sold_then_renovated=("reno_after_sale", "sum"),
    ).reindex(order).fillna(0).astype(int)
    pairs.to_csv(C.REPORT_DIR / "repeat_sales_by_area.csv")

    # Descriptive only (NOT the index): medians for comparable sales by year
    desc = (comp.groupby(["area", "sale_year"])
                .agg(n=("sale_id", "size"),
                     median_price=("sale_price", "median"),
                     median_ppsf=("price_per_sqft", "median"))
                .round(0))
    desc.to_csv(C.REPORT_DIR / "descriptive_medians.csv")

    tables = {
        "funnel_total": _mark(tot),
        "funnel_per_year": _mark(per_year),
        "repeat_sales": _mark(pairs),
        "funnel_by_year": by_year,
        "descriptive_medians": desc,
        "years": (first, last, last - recent_years, last - 1),
    }
    if quiet:
        return tables

    # ------------------------------------------------------------------ print
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)

    print(f"\nSALES FUNNEL, {first}-{last} ({n_years} years), total counts")
    print("Columns are cumulative filters, left to right:")
    for k, lbl, _ in STAGES:
        print(f"  {k:<22} {lbl}")
    print()
    print(_mark(tot).to_string())

    print(f"\nAVERAGE PER YEAR, {last - recent_years}-{last - 1}")
    print(_mark(per_year).to_string())

    print("\nREPEAT SALES (comparable market sales only)")
    print(_mark(pairs).to_string())

    print(f"\nCSV files written to {C.REPORT_DIR}/")
    print("Reading this: an area needs roughly 20+ comparable sales a year to carry its own")
    print("quarterly index. Below that it must borrow strength from the others.")
    return tables


def _mark(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = [f"* {a}" if a in C.TARGET_AREAS else f"  {a}" for a in df.index]
    df.index.name = "area (* = target)"
    return df
