#!/usr/bin/env python3
"""
run_all.py — Run the whole Topic 1 pipeline:

  Step 3   transform.py        CSV → 4★ RDF                    (data/rdf/)
  Step 4a  discover_links.py   find DBpedia/Wikidata links     (data/links/)
  Step 4b  link.py             add links + VoID → 5★ RDF       (data/linked/)
  check    validate.py         quality checks
  Step 5   sparql_terminal.py  interactive SPARQL terminal     (default)
           app.py              web UI + SPARQL endpoint         (--web)

Usage
-----
    python scripts/run_all.py              # pipeline + SPARQL terminal
    python scripts/run_all.py --web        # pipeline + web server / endpoint
    python scripts/run_all.py --offline    # do not contact DBpedia (use cache)
    python scripts/run_all.py --no-serve   # pipeline only
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(script: str, *args, must_pass: bool = True) -> int:
    code = subprocess.run([sys.executable, str(HERE / script), *args]).returncode
    if code != 0 and must_pass:
        print(f"\nERROR: {script} failed with exit code {code}")
        sys.exit(code)
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="link discovery from cache only")
    ap.add_argument("--enrich", action="store_true", help="fetch DBpedia abstracts/images")
    ap.add_argument("--web", action="store_true", help="start the web UI / SPARQL endpoint")
    ap.add_argument("--no-serve", action="store_true", help="stop after validation")
    args = ap.parse_args()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║   Football Linked Data — Full Pipeline (Topic 1)         ║")
    print("╚══════════════════════════════════════════════════════════╝\n")

    print("── Step 3: Transform raw CSV → 4★ RDF ─────────────────────")
    run("transform.py")

    print("\n── Step 4a: Discover links to DBpedia / Wikidata ──────────")
    flags = (["--offline"] if args.offline else []) + (["--enrich"] if args.enrich else [])
    run("discover_links.py", *flags, must_pass=False)      # network problems are not fatal

    print("\n── Step 4b: Add external links + VoID → 5★ Linked Data ────")
    run("link.py")

    print("\n── Quality checks ──────────────────────────────────────────")
    run("validate.py")

    if args.no_serve:
        return
    if args.web:
        print("\n── Step 5: Web UI + SPARQL endpoint ────────────────────────")
        run("app.py")
    else:
        print("\n── Step 5: SPARQL terminal ─────────────────────────────────")
        run("sparql_terminal.py")


if __name__ == "__main__":
    main()
