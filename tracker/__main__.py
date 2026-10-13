"""Command line entry point.

    python -m tracker fetch      download raw data (skips files already present)
    python -m tracker fetch --refresh   re-download everything
    python -m tracker build      clean + join into data/tracker.duckdb
    python -m tracker report     print the sales funnel, write CSVs to reports/
    python -m tracker all        fetch + build + report
"""

import argparse

from . import build, fetch, report


def main() -> None:
    p = argparse.ArgumentParser(prog="tracker", description="Brooklyn brownstone tracker")
    p.add_argument("command", choices=["fetch", "build", "report", "all"])
    p.add_argument("--refresh", action="store_true", help="re-download files already present")
    a = p.parse_args()

    if a.command in ("fetch", "all"):
        fetch.fetch_all(refresh=a.refresh)
    if a.command in ("build", "all"):
        build.build()
    if a.command in ("report", "all"):
        report.run()


if __name__ == "__main__":
    main()
