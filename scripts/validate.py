#!/usr/bin/env python3
"""
validate.py — Quality checks for the 4★ / 5★ datasets.

Checks
  1. Every class / property used in the data is declared in the ontology.
  2. Every local entity has an rdf:type and an rdfs:label.
  3. No dangling references: every local URI used as an object is described.
  4. rdfs:domain / rdfs:range of onto: properties are respected
     (with rdfs:subClassOf reasoning) and literals have the declared datatype.
  5. Data consistency: the goals recorded for a match add up to the score.
  6. 5★ links: owl:sameAs targets are well-formed DBpedia / Wikidata / GeoNames
     URIs, no entity has two different links into the same dataset, and the
     dataset has ≥ 50 links to DBpedia (LOD Cloud criterion).
  7. Every example query in queries/example_queries.sparql runs and returns
     a result (queries tagged  #@requires: network  are skipped).

Exit code 0 = all checks passed, 1 = at least one error.

Usage:  python scripts/validate.py [--dataset 4star|5star]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict

from rdflib import Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from common import DATA, ONTO, PREFIX_BLOCK, load_example_queries, load_graph

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class Report:
    def __init__(self):
        self.errors = 0
        self.warnings = 0

    def check(self, title: str, problems: list[str], warn_only: bool = False, limit: int = 8):
        if not problems:
            print(f"  ✓ {title}")
            return
        mark = "!" if warn_only else "✗"
        print(f"  {mark} {title} — {len(problems)} problem(s)")
        for p in problems[:limit]:
            print(f"      · {p}")
        if len(problems) > limit:
            print(f"      · … and {len(problems) - limit} more")
        if warn_only:
            self.warnings += len(problems)
        else:
            self.errors += len(problems)


def short(u) -> str:
    return str(u).replace(str(DATA), "data:").replace(str(ONTO), "onto:")


def superclasses(g, cls) -> set:
    seen, todo = {cls}, [cls]
    while todo:
        for sup in g.objects(todo.pop(), RDFS.subClassOf):
            if sup not in seen:
                seen.add(sup)
                todo.append(sup)
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["4star", "5star"], default="5star")
    args = ap.parse_args()

    g = load_graph(args.dataset)
    print(f"=== Validating the {args.dataset} dataset ({len(g)} triples incl. ontology) ===\n")
    rep = Report()
    is_local = lambda n: isinstance(n, URIRef) and str(n).startswith(str(DATA))

    # 1. vocabulary declared --------------------------------------------------
    declared = set(g.subjects(RDF.type, OWL.Class)) | set(g.subjects(RDF.type, OWL.ObjectProperty)) \
        | set(g.subjects(RDF.type, OWL.DatatypeProperty)) | set(g.subjects(RDF.type, OWL.NamedIndividual))
    used = {p for s, p, o in g if is_local(s) and str(p).startswith(str(ONTO))} \
        | {o for s, o in g.subject_objects(RDF.type) if is_local(s) and str(o).startswith(str(ONTO))}
    rep.check("all onto: terms used in the data are declared in the ontology",
              [short(t) for t in sorted(used - declared)])

    # 2. type + label -----------------------------------------------------------
    local_subjects = {s for s in g.subjects() if is_local(s)}
    rep.check("every local entity has an rdf:type",
              [short(s) for s in sorted(local_subjects) if (s, RDF.type, None) not in g])
    rep.check("every local entity has an rdfs:label",
              [short(s) for s in sorted(local_subjects) if (s, RDFS.label, None) not in g])

    # 3. dangling references ---------------------------------------------------
    dangling = sorted({f"{short(s)} {short(p)} {short(o)}" for s, p, o in g
                       if is_local(o) and (o, RDF.type, None) not in g})
    rep.check("no dangling references to undescribed local URIs", dangling)

    # 4. domain / range / datatypes ---------------------------------------------
    types = defaultdict(set)
    for s, o in g.subject_objects(RDF.type):
        types[s] |= superclasses(g, o)
    domain_err, range_err, dtype_err = [], [], []
    for prop in set(g.subjects(RDF.type, OWL.ObjectProperty)) | set(g.subjects(RDF.type, OWL.DatatypeProperty)):
        dom, rng = g.value(prop, RDFS.domain), g.value(prop, RDFS.range)
        is_dt = (prop, RDF.type, OWL.DatatypeProperty) in g
        for s, o in g.subject_objects(prop):
            if not is_local(s):
                continue
            if dom is not None and dom not in types[s]:
                domain_err.append(f"{short(s)} {short(prop)} — subject is not a {short(dom)}")
            if rng is None:
                continue
            if is_dt:
                if not isinstance(o, Literal):
                    dtype_err.append(f"{short(s)} {short(prop)} {o} — expected a literal")
                elif rng != XSD.string and o.datatype != rng:
                    dtype_err.append(f"{short(s)} {short(prop)} \"{o}\" — datatype "
                                     f"{short(o.datatype)} ≠ {short(rng)}")
            elif is_local(o) and rng not in types[o]:
                range_err.append(f"{short(s)} {short(prop)} {short(o)} — object is not a {short(rng)}")
    rep.check("rdfs:domain respected", domain_err)
    rep.check("rdfs:range respected", range_err)
    rep.check("literal datatypes match the ontology", dtype_err)

    # 5. goals add up to the score ---------------------------------------------
    goals = Counter()
    for goal, match in g.subject_objects(ONTO.scoredInMatch):
        team = g.value(goal, ONTO.scoredForTeam)
        goals[(match, team)] += 1
    score_err = []
    for m in g.subjects(RDF.type, ONTO.Match):
        for side, score_p in ((ONTO.homeTeam, ONTO.homeScore), (ONTO.awayTeam, ONTO.awayScore)):
            team, score = g.value(m, side), g.value(m, score_p)
            if score is not None and goals[(m, team)] != int(score):
                score_err.append(f"{short(m)}: {short(team)} scored {score} but "
                                 f"{goals[(m, team)]} onto:Goal resources are recorded")
    rep.check("goal records add up to the match scores", sorted(score_err), warn_only=True)

    # 6. links -----------------------------------------------------------------
    if args.dataset == "5star":
        patterns = {
            "DBpedia":  re.compile(r"^http://dbpedia\.org/resource/[^\s<>\"]+$"),
            "Wikidata": re.compile(r"^http://www\.wikidata\.org/entity/Q\d+$"),
            "GeoNames": re.compile(r"^http://sws\.geonames\.org/\d+/$"),
        }
        bad, multi, per_target = [], [], Counter()
        by_entity = defaultdict(lambda: defaultdict(set))
        for s, o in g.subject_objects(OWL.sameAs):
            if not is_local(s):
                continue
            target = next((t for t, rx in patterns.items() if rx.match(str(o))), None)
            if target is None:
                bad.append(f"{short(s)} owl:sameAs <{o}>")
                continue
            per_target[target] += 1
            by_entity[s][target].add(str(o))
        for s, targets in by_entity.items():
            for t, objs in targets.items():
                if len(objs) > 1:
                    multi.append(f"{short(s)} has {len(objs)} {t} links: {', '.join(sorted(objs))}")
        rep.check("owl:sameAs targets are well-formed external URIs", bad)
        rep.check("at most one link per entity and external dataset", multi)
        rep.check(f"≥ 50 links to DBpedia (found {per_target['DBpedia']}; "
                  f"Wikidata {per_target['Wikidata']}, GeoNames {per_target['GeoNames']})",
                  [] if per_target["DBpedia"] >= 50 else ["fewer than 50 DBpedia links"],
                  warn_only=True)
        void_ok = (URIRef("http://semantic-football.org/data"),
                   URIRef("http://rdfs.org/ns/void#sparqlEndpoint"), None) in g
        rep.check("VoID description with void:sparqlEndpoint is present",
                  [] if void_ok else ["data/linked/void.ttl missing — run link.py"])

    # 7. example queries ----------------------------------------------------------
    qerr, skipped = [], 0
    for name, q in load_example_queries().items():
        if "network" in q["requires"] or ("links" in q["requires"] and args.dataset == "4star"):
            skipped += 1
            continue
        try:
            r = g.query(PREFIX_BLOCK + q["query"])
            empty = (r.type == "ASK" and not r.askAnswer) or \
                    (r.type != "ASK" and len(r.graph if r.type in ("CONSTRUCT", "DESCRIBE") else list(r)) == 0)
            if empty:
                qerr.append(f"{name}: returned no result")
        except Exception as e:
            qerr.append(f"{name}: {e}")
    rep.check(f"example queries run and return results ({skipped} skipped: need network/links)", qerr)

    print(f"\n  {rep.errors} error(s), {rep.warnings} warning(s)")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
