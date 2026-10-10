#!/usr/bin/env python3
"""
link.py — Step 4b of Topic 1: add links to other people's data to reach the
5-star Linked Data standard ("link your data to other data to provide context").

Inputs
  data/rdf/football_data.ttl          4★ graph from transform.py
  data/raw/*.csv                      curated wikidata_id / dbpedia_uri / geonames_id
  data/links/discovered_links.csv     links found by discover_links.py (optional)
  data/links/dbpedia_enrichment.json  cached abstracts / thumbnails (optional)

Links written
  owl:sameAs               identity links: club, player, manager, stadium,
                           league, season, city → DBpedia / Wikidata / GeoNames
  onto:nationalityCountry  a nationality ("Norwegian") is not a country, so it
                           is linked to the country with its own property
                           instead of owl:sameAs
  rdfs:comment / foaf:depiction   DBpedia abstract and image (if cached)

Outputs
  data/linked/football_linked.ttl / .nt   5★ dataset
  data/linked/void.ttl                    VoID description: licence, SPARQL
                                          endpoint, dumps, statistics and one
                                          void:Linkset per external dataset

The script never needs the network: discovery and enrichment results come
from the cache files written by discover_links.py, so every run is
reproducible.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
from collections import Counter, defaultdict

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, FOAF, OWL, RDF, RDFS, XSD

from common import (DATA, DATASET_URI, DBR, DISCOVERED_LINKS_CSV, ENRICHMENT_JSON,
                    GEONAMES, LICENSE_URI, LINKED_DIR, LINKED_TTL, ONTO, ONTOLOGY_URI,
                    PROV, RDF_TTL, SCHEMA, VOID, VOID_TTL, WD, bind_prefixes,
                    curated_links, normalize_uri, read_csv)

# Public address of the server started by app.py (used in the VoID file).
PUBLIC_BASE = os.environ.get("FOOTBALL_PUBLIC_BASE", "http://127.0.0.1:5000").rstrip("/")

TARGETS = {   # external dataset → (namespace, VoID URI of that dataset)
    "DBpedia":  ("http://dbpedia.org/resource/",    "http://dbpedia.org/void/Dataset"),
    "Wikidata": ("http://www.wikidata.org/entity/", "http://www.wikidata.org/"),
    "GeoNames": (str(GEONAMES),                     "http://sws.geonames.org/"),
}


def target_of(uri: str) -> str | None:
    for name, (ns, _) in TARGETS.items():
        if uri.startswith(ns):
            return name
    return None


class Linker:
    def __init__(self):
        if not RDF_TTL.exists():
            sys.exit(f"ERROR: {RDF_TTL} not found. Run transform.py first.")
        self.g = bind_prefixes(Graph())
        self.g.parse(RDF_TTL, format="turtle")
        self.base_size = len(self.g)
        self.stats = Counter()
        print(f"  Loaded {self.base_size} triples from the 4★ graph.")

    def exists(self, local: str) -> bool:
        return (URIRef(local), RDF.type, None) in self.g

    def same_as(self, local: str, external: str, source: str) -> bool:
        s, o = URIRef(local), URIRef(external)
        if (s, OWL.sameAs, o) in self.g:
            return False
        self.g.add((s, OWL.sameAs, o))
        self.stats[source] += 1
        return True

    # ── 1. curated links from the CSV files ──────────────────
    def add_curated(self):
        skipped = 0
        for local, external in curated_links():
            if not self.exists(local):
                skipped += 1
                continue
            self.same_as(local, external, "curated")
        print(f"  Added {self.stats['curated']} curated owl:sameAs links"
              + (f" ({skipped} skipped: unknown local entity)" if skipped else "") + ".")

    # ── 2. nationality → country ─────────────────────────────
    def add_nationality_countries(self):
        n = 0
        for row in read_csv("nationalities.csv"):
            nat = DATA[f"nationality/{row['nationality_id']}"]
            if (nat, RDF.type, ONTO.Nationality) not in self.g:
                continue
            for ext in (WD[row["country_wikidata_id"]] if row.get("country_wikidata_id") else None,
                        URIRef(normalize_uri(row["country_dbpedia_uri"]))
                        if row.get("country_dbpedia_uri") else None):
                if ext is None:
                    continue
                self.g.add((nat, ONTO.nationalityCountry, ext))
                self.g.add((ext, RDF.type, SCHEMA.Country))
                self.g.add((ext, RDFS.label, Literal(row["country_name"], lang="en")))
                n += 1
        self.stats["nationality"] = n
        print(f"  Added {n} onto:nationalityCountry links (nationality → Wikidata/DBpedia country).")

    # ── 3. automatically discovered links ────────────────────
    def add_discovered(self):
        if not DISCOVERED_LINKS_CSV.exists():
            print("  No discovered links yet (run discover_links.py with internet access).")
            return
        conflicts = 0
        with open(DISCOVERED_LINKS_CSV, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["status"] == "accepted"]
        for r in rows:
            local = r["local_uri"]
            if not self.exists(local):
                continue
            existing = {str(o) for o in self.g.objects(URIRef(local), OWL.sameAs)}
            for ext in (r["dbpedia_uri"], r["wikidata_uri"]):
                if not ext:
                    continue
                same_target = [e for e in existing if target_of(e) == target_of(ext)]
                if same_target and normalize_uri(ext) not in map(normalize_uri, same_target):
                    conflicts += 1      # curated link wins over an automatic one
                    continue
                self.same_as(local, normalize_uri(ext), "discovered")
        print(f"  Added {self.stats['discovered']} discovered owl:sameAs links from "
              f"{len(rows)} accepted matches"
              + (f" ({conflicts} conflicting with curated links were ignored)" if conflicts else "")
              + ".")

    # ── 4. DBpedia enrichment (from cache) ───────────────────
    def add_enrichment(self):
        if not ENRICHMENT_JSON.exists():
            print("  No DBpedia enrichment cache (run discover_links.py --enrich).")
            return
        cache = json.loads(ENRICHMENT_JSON.read_text(encoding="utf-8"))
        n = 0
        for s, o in list(self.g.subject_objects(OWL.sameAs)):
            info = cache.get(normalize_uri(str(o)))
            if not info or not str(s).startswith(str(DATA)):
                continue
            if info.get("comment"):
                self.g.add((s, RDFS.comment, Literal(info["comment"], lang="en")))
                n += 1
            if info.get("thumbnail"):
                self.g.add((s, FOAF.depiction, URIRef(info["thumbnail"])))
                n += 1
        print(f"  Added {n} rdfs:comment / foaf:depiction triples from the DBpedia cache.")

    # ── 5. VoID description ──────────────────────────────────
    def build_void(self) -> Graph:
        g, v = self.g, bind_prefixes(Graph())
        ds = URIRef(DATASET_URI)
        today = Literal(dt.date.today().isoformat(), datatype=XSD.date)

        v.add((ds, RDF.type, VOID.Dataset))
        v.add((ds, RDF.type, PROV.Entity))
        v.add((ds, DCTERMS.title, Literal("Football Linked Data (Premier League 2023-24)", lang="en")))
        v.add((ds, DCTERMS.description, Literal(
            "Clubs, players, managers, stadiums, matches, goals and transfers of the 2023-24 "
            "Premier League as 5-star Linked Data, linked to DBpedia, Wikidata and GeoNames.",
            lang="en")))
        v.add((ds, DCTERMS.license, URIRef(LICENSE_URI)))
        v.add((ds, DCTERMS.creator, Literal("Semantic Web course project (IT6390E)")))
        v.add((ds, DCTERMS.modified, today))
        v.add((ds, DCTERMS.subject, DBR["Association_football"]))
        v.add((ds, FOAF.homepage, URIRef(PUBLIC_BASE + "/")))
        v.add((ds, VOID.sparqlEndpoint, URIRef(PUBLIC_BASE + "/sparql")))
        for f in ("football_linked.ttl", "football_linked.nt"):
            v.add((ds, VOID.dataDump, URIRef(f"{PUBLIC_BASE}/dump/{f}")))
        v.add((ds, VOID.uriSpace, Literal(str(DATA))))
        v.add((ds, VOID.exampleResource, DATA["player/erling_haaland"]))
        v.add((ds, VOID.exampleResource, DATA["club/arsenal"]))
        for vocab in (ONTOLOGY_URI + "#", str(SCHEMA), str(FOAF), "http://dbpedia.org/ontology/"):
            v.add((ds, VOID.vocabulary, URIRef(vocab)))

        # statistics
        local = [s for s in set(g.subjects(RDF.type, None)) if str(s).startswith(str(DATA))]
        v.add((ds, VOID.triples, Literal(len(g), datatype=XSD.integer)))
        v.add((ds, VOID.entities, Literal(len(local), datatype=XSD.integer)))
        v.add((ds, VOID.distinctSubjects, Literal(len(set(g.subjects())), datatype=XSD.integer)))
        v.add((ds, VOID.properties, Literal(len(set(g.predicates())), datatype=XSD.integer)))
        per_class = Counter(o for s, o in g.subject_objects(RDF.type)
                            if str(s).startswith(str(DATA)) and str(o).startswith(str(ONTO)))
        v.add((ds, VOID.classes, Literal(len(per_class), datatype=XSD.integer)))
        for cls, n in sorted(per_class.items()):
            part = URIRef(f"{DATASET_URI}#class-{str(cls).split('#')[-1]}")
            v.add((ds, VOID.classPartition, part))
            v.add((part, VOID["class"], cls))
            v.add((part, VOID.entities, Literal(n, datatype=XSD.integer)))

        # one void:Linkset per (target dataset, link predicate)
        counts: dict[tuple[str, URIRef], int] = defaultdict(int)
        for pred in (OWL.sameAs, ONTO.nationalityCountry):
            for s, o in g.subject_objects(pred):
                t = target_of(str(o))
                if t and str(s).startswith(str(DATA)):
                    counts[(t, pred)] += 1
        for (t, pred), n in sorted(counts.items()):
            ls = URIRef(f"{DATASET_URI}#linkset-{t.lower()}-{pred.split('#')[-1]}")
            target = URIRef(TARGETS[t][1])
            v.add((ds, VOID.subset, ls))
            v.add((ls, RDF.type, VOID.Linkset))
            v.add((ls, DCTERMS.title, Literal(f"{pred.split('#')[-1]} links to {t}")))
            v.add((ls, VOID.subjectsTarget, ds))
            v.add((ls, VOID.objectsTarget, target))
            v.add((ls, VOID.linkPredicate, pred))
            v.add((ls, VOID.triples, Literal(n, datatype=XSD.integer)))
            v.add((target, RDF.type, VOID.Dataset))
            v.add((target, DCTERMS.title, Literal(t)))

        # provenance of the pipeline run
        act = BNode()
        v.add((ds, PROV.wasGeneratedBy, act))
        v.add((act, RDF.type, PROV.Activity))
        v.add((act, RDFS.label, Literal("transform.py → discover_links.py → link.py")))
        v.add((act, PROV.endedAtTime, Literal(dt.datetime.now().replace(microsecond=0).isoformat(),
                                              datatype=XSD.dateTime)))
        self.linkset_counts = counts
        return v


def main():
    print("=== Step 4b — Establishing external links (5★ Linked Data) ===\n")
    linker = Linker()
    linker.add_curated()
    linker.add_nationality_countries()
    linker.add_discovered()
    linker.add_enrichment()
    void = linker.build_void()

    LINKED_DIR.mkdir(parents=True, exist_ok=True)
    linker.g.serialize(destination=str(LINKED_TTL), format="turtle")
    linker.g.serialize(destination=str(LINKED_TTL.with_suffix(".nt")), format="nt", encoding="utf-8")
    void.serialize(destination=str(VOID_TTL), format="turtle")

    total_links = sum(linker.linkset_counts.values())
    print(f"\n  Links per external dataset:")
    for (t, pred), n in sorted(linker.linkset_counts.items()):
        print(f"    {t:<9} {pred.split('#')[-1]:<20} {n}")
    dbp = sum(n for (t, _), n in linker.linkset_counts.items() if t == "DBpedia")
    print(f"  Total external links: {total_links}"
          f"  (LOD Cloud asks for ≥ 50 links to one dataset — DBpedia: {dbp})")
    print(f"\n✓ 5★ dataset: {len(linker.g)} triples → {LINKED_TTL}")
    print(f"✓ N-Triples → {LINKED_TTL.with_suffix('.nt')}")
    print(f"✓ VoID      → {VOID_TTL} ({len(void)} triples)")


if __name__ == "__main__":
    main()
