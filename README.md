# Brooklyn Brownstone Tracker

Stage 1: the data layer. It downloads public NYC data, cleans it, and builds
one database of brownstone sales in your target neighborhoods (plus comparison
neighborhoods), with every flag the model will need later. It also prints the
"how much data do we really have" funnel.

## Running in Google Colab

1. Open `brownstone_tracker_colab.ipynb` in Colab: File → Open notebook →
   GitHub tab → `kaetowicu/brownstone-tracker` (or upload the file).
2. If the repository is private, add a GitHub token named `GITHUB_TOKEN` in
   Colab's Secrets panel (key icon) and enable notebook access.
3. Runtime → Run all, and approve the Drive connection.

Each run pulls the latest code from GitHub, so updates arrive automatically.

Downloads, reports and a copy of the database are kept in
`My Drive/brownstone_tracker/`, so they survive Colab disconnects. The
locations can be changed with the `TRACKER_RAW_DIR`, `TRACKER_DB_PATH` and
`TRACKER_REPORT_DIR` environment variables.

## Setup on your own computer (once)

Requires Python 3.10 or newer.

```bash
cd brownstone-tracker
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python -m tracker all       # download (~10-20 min first time), build, report
```

Or step by step:

```bash
python -m tracker fetch     # downloads into data/raw/ (skips files already there)
python -m tracker build     # writes data/tracker.duckdb
python -m tracker report    # prints the funnel, writes CSVs to reports/
```

Re-run `fetch` monthly to pick up new sales (the rolling 12-month file is
always re-downloaded). Optional: a free NYC Open Data app token raises rate
limits: `export SOCRATA_APP_TOKEN=...`.

**If a sales file fails to download** (nyc.gov sometimes blocks scripts): years
2016+ fall back to the Open Data copy automatically. For earlier years, open the
[annualized sales page](https://www.nyc.gov/site/finance/property/property-annualized-sales-update.page)
in a browser, download the Brooklyn file, and save it into `data/raw/sales/` as
`brooklyn_<year>.xls`. Then run `build` again.

## Data sources

| Data | Source | Used for |
|---|---|---|
| Brooklyn sales 2003–present | Dept. of Finance yearly files + rolling 12-month file | Every recorded sale |
| PLUTO (version 26v2 at time of writing) | NYC Open Data `64uk-42ks` | Width, floors, size, year built, attached/detached, built vs allowed floor area, current historic district |
| Historic district boundaries | NYC Open Data `skyk-mpzq` | How deep inside a district each lot sits |
| DOB job filings (BIS and DOB NOW) | NYC Open Data `ic3t-wcy2`, `w9ak-ipjd` | Renovations before or after each sale, unit conversions |

## What the build does

- **Parses 20+ years of spreadsheets** whose layout changed several times, by
  finding the header row and matching columns by keyword.
- **Assigns each sale to an area.** District membership wins over the city's
  neighborhood label, so a Stuyvesant Heights house is Bed-Stuy and a lot
  inside the Park Slope districts is Park Slope whatever its label says.
  Areas are defined in `tracker/config.py`; edit there to add or split areas.
- **Flags non-market sales**: under $100K, multi-lot package deals, and sales
  far below the area's typical $/sq ft that year.
- **Flags brownstone-type houses**: 1–3 family, built 1931 or earlier,
  attached or semi-attached, 2–5 floors, 11–40 ft wide.
- **Measures depth inside the historic district**: feet from the nearest
  edge of any designated district (negative if outside). Adjacent districts
  are merged so the seam between, say, Park Slope and its extension isn't
  treated as an edge.
- **Adds renovation context** from DOB: a major renovation in the 4 years
  before a sale (probably sold renovated) or within 18 months after (probably
  sold needing work).
- **Links repeat sales** of the same house and flags flips under 24 months.

## Database tables (`data/tracker.duckdb`)

| Table | Contents |
|---|---|
| `sales` | Sales in your areas, joined to lot data, with all flags. Main table. |
| `sales_all` | Every Brooklyn sale record, tidied |
| `lots` | All Brooklyn lots with district depth, width, unused floor area |
| `districts` | Designated historic districts and designation dates |
| `dob_jobs` | Renovation, new building and demolition filings |

Explore it with any DuckDB client, or in Python:

```python
import duckdb
con = duckdb.connect("data/tracker.duckdb", read_only=True)
con.sql("""
  SELECT area, sale_year, count(*) n, median(sale_price) med
  FROM sales
  WHERE is_market_sale AND is_brownstone_type AND family <= 2
  GROUP BY ALL ORDER BY ALL
""").show()
```

## Reading the report

The funnel applies the filters one at a time, left to right, so you can see
where sales drop out. The "average per year" table uses the last five full
years. Rough guide: an area needs about 20 comparable sales a year to support
its own quarterly price index; below that, the model must pool it with the
other areas. Repeat-sale pairs show whether a repeat-sales index is viable.

The budget column uses nominal prices, so $2M in 2005 counts the same as $2M
today. The stage 2 model fixes this by adjusting for each area's price level.

## Tests

```bash
python -m pytest tests/
```

The test builds fake files in the city's real formats (old `.xls` with title
rows, newer `.xlsx`, PLUTO, district polygons, DOB filings) and runs the whole
pipeline offline.

## Next stages

2. **Model**: hierarchical hedonic price index per area, smoothed quarterly,
   with error bands; drawdown and volatility per area, including 2008–2012.
3. **Protection score per block**: depth in district, soft sites nearby,
   park and waterfront frontage, pending rezonings.
4. **Listing evaluator**: address + asking price → fair value range,
   protection score, red flags.
5. **Dashboard**: trends, divergence between areas, recent comparable sales map.
