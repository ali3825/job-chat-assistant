#!/usr/bin/env python3
"""Collect real job listings from public job APIs into data/jobs.json.

Usage (from the repo root):
    python scripts/collect_jobs.py                      # all keyless sources
    ADZUNA_APP_ID=... ADZUNA_APP_KEY=... python scripts/collect_jobs.py --sources adzuna,arbeitnow
    python scripts/collect_jobs.py --rebuild-only       # re-dedupe/validate already collected data, no network
Re-running resumes from data/raw/state.json and never duplicates records.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collector.common import HttpClient  # noqa: E402
from collector.pipeline import ROOT, Pipeline  # noqa: E402
from collector.sources import all_sources  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", default="", help="comma list (default: every source that is available)")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "jobs.json")
    ap.add_argument("--work-dir", type=Path, default=ROOT / "data" / "raw")
    ap.add_argument("--target", type=int, default=2000, help="max jobs in final file")
    ap.add_argument("--min", dest="min_total", type=int, default=1500)
    ap.add_argument("--per-source-cap", type=int, default=1000)
    ap.add_argument("--no-tech-filter", action="store_true", help="keep non-tech listings too")
    ap.add_argument("--rebuild-only", action="store_true", help="skip fetching; just dedupe/validate/write")
    ap.add_argument("--contact", default=os.getenv("COLLECTOR_CONTACT", "set-COLLECTOR_CONTACT-env-var"),
                    help="email/URL placed in the User-Agent so sites can reach you")
    ap.add_argument("--adzuna-country", default="in")
    ap.add_argument("--adzuna-what", default="software developer")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    srcs = all_sources(args.adzuna_country, args.adzuna_what)
    wanted = [s.strip() for s in args.sources.split(",") if s.strip()] or list(srcs)
    unknown = [w for w in wanted if w not in srcs]
    if unknown:
        print(f"Unknown sources: {unknown}. Choose from {list(srcs)}", file=sys.stderr)
        return 1
    http = HttpClient(args.contact)
    pipe = Pipeline(args.work_dir, args.out, http, [srcs[w] for w in wanted], target=args.target, min_total=args.min_total,
                    per_source_cap=args.per_source_cap, tech_only=not args.no_tech_filter)
    try:
        if not args.rebuild_only:
            pipe.run()
        report = pipe.build()
    finally:
        http.close()
    print(f"\nWrote {report['total_jobs']} jobs to {args.out}")
    print("By source:", report["by_source"])
    if not report["meets_minimum"]:
        print("WARNING:", report["shortfall_note"], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
