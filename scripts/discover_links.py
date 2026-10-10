#!/usr/bin/env python3
"""
discover_links.py — Step 4a of Topic 1: *find* links to other datasets
automatically instead of only typing them by hand.

It works like a small Silk link-discovery rule (http://silkframework.org/):

  1. Candidate generation
       For every local entity (club, player, manager, stadium, league, season,
       city) search DBpedia by name with the DBpedia Lookup service, then fetch
       each candidate's rdf:type, rdfs:label, dbo:birthDate and owl:sameAs →
       Wikidata from the DBpedia SPARQL endpoint.
  2. Filtering
       Keep only candidates whose DBpedia class fits the local class
       (e.g. onto:Player ↔ dbo:SoccerPlayer).
  3. Scoring  (0 … 1)
       name   = similarity of normalised names (accent-insensitive, "F.C."/"FC"
                and "(footballer)" removed; max of edit-ratio and token overlap)
       birth  = 1 if dbo:birthDate equals our date of birth, 0 otherwise
       score  = 0.6·name + 0.4·birth   when both sides have a birth date
              = name                   otherwise
       A different birth date rejects the candidate outright.
  4. Decision
       score ≥ 0.90 → accepted   (used by link.py)
       score ≥ 0.75 → review     (listed, not used)
       two candidates within 0.05 of each other → review (ambiguous)
  5. Evaluation
       Accepted links are compared with the hand-curated links stored in the
       CSV files → precision / recall / F1, plus a check that the curated
       Wikidata IDs agree with DBpedia's own owl:sameAs. The report is written
       to data/links/evaluation.md.

All HTTP responses are cached in data/links/dbpedia_cache.json, so the result
is reproducible and the pipeline still works offline afterwards.

Usage
-----
    python scripts/discover_links.py              # online, uses + updates cache
    python scripts/discover_links.py --offline    # cache only (no network)
    python scripts/discover_links.py --enrich     # also fetch abstracts/thumbnails
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

from rdflib import URIRef
from rdflib.namespace import RDF

from common import (DATA, DBPEDIA_CACHE_JSON, DISCOVERED_LINKS_CSV,
                    ENRICHMENT_JSON, LINKS_DIR, ONTO, curated_links,
                    load_graph, normalize_uri)

LOOKUP_URL = "https://lookup.dbpedia.org/api/search"
SPARQL_URL = "https://dbpedia.org/sparql"
USER_AGENT = "FootballLinkedData/1.1 (Semantic Web course project)"

ACCEPT_THRESHOLD = 0.90
REVIEW_THRESHOLD = 0.75
AMBIGUITY_MARGIN = 0.05   # best and runner-up closer than this → manual review

DBO = "http://dbpedia.org/ontology/"
# local class → (DBpedia Lookup typeName, acceptable DBpedia classes)
TYPE_MAP = {
    ONTO.FootballClub: ("SoccerClub",   {"SoccerClub", "SportsTeam"}),
    ONTO.Player:       ("SoccerPlayer", {"SoccerPlayer"}),
    ONTO.Manager:      ("Person",       {"SoccerManager", "SoccerPlayer", "Person"}),
    ONTO.Stadium:      ("Stadium",      {"Stadium", "Venue", "SportFacility"}),
    ONTO.League:       ("SoccerLeague", {"SoccerLeague", "SportsLeague"}),
    ONTO.Season:       ("",             {"SoccerLeagueSeason", "FootballLeagueSeason",
                                         "SportsSeason", "SportsTeamSeason"}),
    ONTO.City:         ("City",         {"City", "Settlement", "Town", "PopulatedPlace"}),
}

NOISE = re.compile(r"\b(f\.?\s?c\.?|a\.?\s?f\.?\s?c\.?|c\.?\s?f\.?|s\.?\s?l\.?|"
                   r"football club|club de futbol|footballer|goalkeeper|stadium)\b")


# ─────────────────────────────────────────────────────────────
#  String similarity
# ─────────────────────────────────────────────────────────────
def norm_name(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)                 # Lookup highlights <B>…</B>
    text = re.sub(r"\([^)]*\)", " ", text)              # "(footballer, born 1996)"
    text = text.replace("ø", "o").replace("Ø", "O").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = NOISE.sub(" ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def name_similarity(a: str, b: str) -> float:
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return max(ratio, overlap)


# ─────────────────────────────────────────────────────────────
#  HTTP with cache
# ─────────────────────────────────────────────────────────────
class Fetcher:
    def __init__(self, offline: bool):
        self.offline = offline
        self.cache = {}
        if DBPEDIA_CACHE_JSON.exists():
            self.cache = json.loads(DBPEDIA_CACHE_JSON.read_text(encoding="utf-8"))
        self.errors = 0
        self.requests = 0

    def save(self):
        if not self.cache:
            return
        LINKS_DIR.mkdir(parents=True, exist_ok=True)
        DBPEDIA_CACHE_JSON.write_text(json.dumps(self.cache, indent=1, ensure_ascii=False,
                                                 sort_keys=True), encoding="utf-8")

    def get_json(self, url: str, params: dict) -> dict | None:
        key = url + "?" + urllib.parse.urlencode(sorted(params.items()))
        if key in self.cache:
            return self.cache[key]
        if self.offline or self.errors >= 3:
            return None
        req = urllib.request.Request(key, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:                      # network down, 5xx, …
            self.errors += 1
            print(f"    ! request failed ({exc.__class__.__name__}: {exc})")
            if self.errors >= 3:
                print("    ! DBpedia unreachable — continuing with cached data only.")
            return None
        self.requests += 1
        self.cache[key] = data
        time.sleep(0.2)                               # be polite to DBpedia
        return data

    def lookup(self, label: str, type_name: str) -> list[str]:
        params = {"query": label, "maxResults": "10", "format": "JSON"}
        if type_name:
            params["typeName"] = type_name
        data = self.get_json(LOOKUP_URL, params) or {}
        uris = []
        for doc in data.get("docs", []):
            for r in doc.get("resource", []):
                uris.append(normalize_uri(r))
        return uris

    def sparql(self, query: str) -> list[dict]:
        data = self.get_json(SPARQL_URL, {"query": query,
                                          "format": "application/sparql-results+json"})
        return (data or {}).get("results", {}).get("bindings", [])

    def details(self, uris: list[str]) -> dict[str, dict]:
        """Types, English label, birth date and Wikidata sameAs for DBpedia resources."""
        out: dict[str, dict] = {}
        for i in range(0, len(uris), 20):
            chunk = uris[i:i + 20]
            values = " ".join(f"<{iri_escape(u)}>" for u in chunk)
            rows = self.sparql(f"""
                SELECT ?s ?type ?label ?birth ?same WHERE {{
                  VALUES ?s {{ {values} }}
                  OPTIONAL {{ ?s a ?type . FILTER(STRSTARTS(STR(?type), "{DBO}")) }}
                  OPTIONAL {{ ?s <http://www.w3.org/2000/01/rdf-schema#label> ?label .
                              FILTER(LANG(?label) = "en") }}
                  OPTIONAL {{ ?s <{DBO}birthDate> ?birth }}
                  OPTIONAL {{ ?s <http://www.w3.org/2002/07/owl#sameAs> ?same .
                              FILTER(STRSTARTS(STR(?same), "http://www.wikidata.org/entity/")) }}
                }}""")
            for b in rows:
                s = normalize_uri(b["s"]["value"])
                d = out.setdefault(s, {"types": set(), "label": "", "birth": "", "wikidata": ""})
                if "type" in b:
                    d["types"].add(b["type"]["value"][len(DBO):])
                if "label" in b:
                    d["label"] = b["label"]["value"]
                if "birth" in b:
                    d["birth"] = b["birth"]["value"][:10]
                if "same" in b:
                    d["wikidata"] = b["same"]["value"]
        return out


def iri_escape(u: str) -> str:
    """Characters that are illegal inside <…> in SPARQL must be %-encoded."""
    return re.sub(r'[<>"{}|^`\\ ]', lambda m: urllib.parse.quote(m.group(0)), u)


# ─────────────────────────────────────────────────────────────
#  Local entities
# ─────────────────────────────────────────────────────────────
def local_entities(g) -> list[dict]:
    ents = []
    for cls in TYPE_MAP:
        for s in sorted(g.subjects(RDF.type, cls)):
            if not str(s).startswith(str(DATA)):
                continue
            label = str(g.value(s, ONTO.name) or "")
            if cls == ONTO.Season:
                label = str(g.value(s, URIRef("http://www.w3.org/2000/01/rdf-schema#label")) or label)
            birth = g.value(s, ONTO.dateOfBirth)
            ents.append({"uri": str(s), "cls": cls, "label": label,
                         "birth": str(birth) if birth else ""})
    return ents


def search_label(e: dict) -> str:
    if e["cls"] == ONTO.Season:
        # "Premier League 2023-24" → DBpedia names it "2023–24 Premier League"
        m = re.search(r"(\d{4})-(\d{2})", e["label"])
        return f"{m.group(1)}–{m.group(2)} Premier League" if m else e["label"]
    return e["label"]


def score_candidate(e: dict, cand: dict) -> tuple[float, str]:
    _, ok_types = TYPE_MAP[e["cls"]]
    if cand["types"] and not (cand["types"] & ok_types):
        return 0.0, "type mismatch"
    name = name_similarity(search_label(e), cand["label"] or cand["uri"].rsplit("/", 1)[-1])
    if e["birth"] and cand["birth"]:
        if e["birth"] != cand["birth"]:
            return 0.0, f"birth date differs ({cand['birth']})"
        return 0.6 * name + 0.4, "name + birth date"
    return name, "name only"


# ─────────────────────────────────────────────────────────────
#  Evaluation against curated links
# ─────────────────────────────────────────────────────────────
def evaluate(results: list[dict], details: dict) -> str:
    curated_dbp: dict[str, str] = {}
    curated_wd: dict[str, str] = {}
    for local, ext in curated_links():
        if ext.startswith("http://dbpedia.org/resource/"):
            curated_dbp[local] = normalize_uri(ext)
        elif ext.startswith("http://www.wikidata.org/entity/"):
            curated_wd[local] = ext

    accepted = {r["local_uri"]: r for r in results if r["status"] == "accepted"}
    tp = fp = fn = 0
    rows_fp, rows_fn, new_links = [], [], []
    for local, gold in curated_dbp.items():
        found = accepted.get(local)
        if found and normalize_uri(found["dbpedia_uri"]) == gold:
            tp += 1
        elif found:
            fp += 1
            rows_fp.append((local, found["dbpedia_uri"], gold))
        else:
            fn += 1
            rows_fn.append((local, gold))
    for local, r in accepted.items():
        if local not in curated_dbp:
            new_links.append(r)

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    # do the curated Wikidata IDs agree with DBpedia's own owl:sameAs?
    wd_checked = wd_agree = 0
    wd_mismatch = []
    for local, dbp in curated_dbp.items():
        d = details.get(dbp)
        if d and d["wikidata"] and local in curated_wd:
            wd_checked += 1
            if d["wikidata"] == curated_wd[local]:
                wd_agree += 1
            else:
                wd_mismatch.append((local, curated_wd[local], d["wikidata"]))
    missing_dbp = [(l, u) for l, u in curated_dbp.items() if u not in details]

    short = lambda u: u.replace(str(DATA), "data:").replace("http://dbpedia.org/resource/", "dbr:") \
                       .replace("http://www.wikidata.org/entity/", "wd:")
    lines = [
        "# Link discovery — evaluation report", "",
        "Automatically discovered DBpedia links (status = accepted, score ≥ "
        f"{ACCEPT_THRESHOLD}) compared with the hand-curated links in `data/raw/*.csv`.", "",
        "| Metric | Value |", "|---|---|",
        f"| Curated DBpedia links (gold standard) | {len(curated_dbp)} |",
        f"| Accepted automatic links | {len(accepted)} |",
        f"| True positives | {tp} |", f"| False positives | {fp} |", f"| False negatives | {fn} |",
        f"| **Precision** | {precision:.3f} |", f"| **Recall** | {recall:.3f} |", f"| **F1** | {f1:.3f} |",
        f"| New links for entities without a curated link | {len(new_links)} |",
        f"| Curated Wikidata IDs confirmed by DBpedia owl:sameAs | {wd_agree}/{wd_checked} |", "",
    ]
    if not details:
        lines += ["> ⚠ No DBpedia data in the cache — run this script with internet access "
                  "(without `--offline`) to obtain real figures.", ""]
    if new_links:
        lines += ["## New links found automatically", "", "| Local | DBpedia | Wikidata | Score |",
                  "|---|---|---|---|"]
        lines += [f"| {short(r['local_uri'])} | {short(r['dbpedia_uri'])} | {short(r['wikidata_uri'])} "
                  f"| {r['score']} |" for r in new_links]
        lines.append("")
    if rows_fp:
        lines += ["## Disagreements (automatic ≠ curated)", "", "| Local | Automatic | Curated |",
                  "|---|---|---|"]
        lines += [f"| {short(a)} | {short(b)} | {short(c)} |" for a, b, c in rows_fp]
        lines.append("")
    if rows_fn:
        lines += ["## Curated links not found automatically", ""]
        lines += [f"- {short(a)} → {short(b)}" for a, b in rows_fn]
        lines.append("")
    if missing_dbp and details:
        lines += ["## Curated DBpedia URIs that DBpedia does not know (possible typos)", ""]
        lines += [f"- {short(a)} → {short(b)}" for a, b in missing_dbp]
        lines.append("")
    if wd_mismatch:
        lines += ["## Curated Wikidata IDs that disagree with DBpedia", "",
                  "| Local | Curated | DBpedia says |", "|---|---|---|"]
        lines += [f"| {short(a)} | {short(b)} | {short(c)} |" for a, b, c in wd_mismatch]
        lines.append("")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
#  Enrichment (abstract + thumbnail), cached for link.py
# ─────────────────────────────────────────────────────────────
def enrich(fetcher: Fetcher, uris: list[str]):
    out = {}
    if ENRICHMENT_JSON.exists():
        out = json.loads(ENRICHMENT_JSON.read_text(encoding="utf-8"))
    todo = [u for u in uris if u not in out]
    for i in range(0, len(todo), 20):
        values = " ".join(f"<{iri_escape(u)}>" for u in todo[i:i + 20])
        rows = fetcher.sparql(f"""
            SELECT ?s ?comment ?thumb WHERE {{
              VALUES ?s {{ {values} }}
              OPTIONAL {{ ?s <http://www.w3.org/2000/01/rdf-schema#comment> ?comment .
                          FILTER(LANG(?comment) = "en") }}
              OPTIONAL {{ ?s <{DBO}thumbnail> ?thumb }}
            }}""")
        for b in rows:
            d = out.setdefault(normalize_uri(b["s"]["value"]), {})
            if "comment" in b:
                d["comment"] = b["comment"]["value"]
            if "thumb" in b:
                d["thumbnail"] = b["thumb"]["value"]
    ENRICHMENT_JSON.write_text(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True),
                               encoding="utf-8")
    print(f"  Enrichment cache: {len(out)} DBpedia resources → {ENRICHMENT_JSON}")


# ─────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline", action="store_true", help="use cached responses only")
    ap.add_argument("--enrich", action="store_true",
                    help="also cache rdfs:comment and dbo:thumbnail for linked resources")
    args = ap.parse_args()

    print("=== Step 4a — Automatic link discovery against DBpedia ===\n")
    g = load_graph("4star", with_ontology=False)
    fetcher = Fetcher(args.offline)
    ents = local_entities(g)
    print(f"  {len(ents)} local entities to match")

    # 1. candidate generation
    cand_uris: dict[str, list[str]] = {}
    for e in ents:
        type_name, _ = TYPE_MAP[e["cls"]]
        cand_uris[e["uri"]] = fetcher.lookup(search_label(e), type_name)
    gold = {l: normalize_uri(u) for l, u in curated_links() if "dbpedia.org" in u}
    all_uris = sorted({u for us in cand_uris.values() for u in us} | set(gold.values()))
    details = fetcher.details(all_uris) if all_uris else {}
    if not details and set(gold.values()):
        details = fetcher.details(sorted(set(gold.values())))

    if not details:
        fetcher.save()
        print("\n  ⚠ No DBpedia data available (offline or unreachable) — existing results "
              "were left untouched.\n    Run this script again with internet access.")
        return 0

    # 2–4. filter, score, decide
    results = []
    for e in ents:
        scored = []
        for u in dict.fromkeys(cand_uris[e["uri"]]):          # unique, Lookup order
            cand = dict(details.get(u, {"types": set(), "label": "", "birth": "", "wikidata": ""}))
            cand["uri"] = u
            score, reason = score_candidate(e, cand)
            scored.append((score, reason, cand))
        scored.sort(key=lambda t: -t[0])                     # stable: keeps Lookup rank on ties
        if not scored or scored[0][0] < REVIEW_THRESHOLD:
            continue
        score, reason, cand = scored[0]
        ambiguous = len(scored) > 1 and scored[0][0] - scored[1][0] < AMBIGUITY_MARGIN
        if ambiguous:
            reason += f"; ambiguous with {scored[1][2]['uri'].rsplit('/', 1)[-1]}"
        results.append({
            "local_uri": e["uri"], "local_label": e["label"],
            "local_type": str(e["cls"]).split("#")[-1],
            "dbpedia_uri": cand["uri"], "dbpedia_label": cand["label"],
            "wikidata_uri": cand["wikidata"], "score": f"{score:.3f}",
            "status": "accepted" if score >= ACCEPT_THRESHOLD and not ambiguous else "review",
            "method": reason,
        })

    LINKS_DIR.mkdir(parents=True, exist_ok=True)
    with open(DISCOVERED_LINKS_CSV, "w", newline="", encoding="utf-8") as f:
        fields = ["local_uri", "local_label", "local_type", "dbpedia_uri", "dbpedia_label",
                  "wikidata_uri", "score", "status", "method"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    n_acc = sum(r["status"] == "accepted" for r in results)
    print(f"  {n_acc} accepted, {len(results) - n_acc} to review → {DISCOVERED_LINKS_CSV}")

    report = evaluate(results, details)
    (LINKS_DIR / "evaluation.md").write_text(report, encoding="utf-8")
    print(f"  Evaluation report → {LINKS_DIR / 'evaluation.md'}")
    for line in report.splitlines():
        if line.startswith("| **"):
            print("   ", line.replace("|", " ").replace("**", "").strip())

    if args.enrich:
        uris = sorted(set(gold.values()) | {r["dbpedia_uri"] for r in results
                                           if r["status"] == "accepted"})
        enrich(fetcher, uris)

    fetcher.save()
    print(f"\n  HTTP requests made: {fetcher.requests} (cache: {DBPEDIA_CACHE_JSON.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
