#!/usr/bin/env python3
"""
common.py — Shared configuration for every script in the pipeline:
paths, namespaces, the auto-injected PREFIX block, CSV helpers, graph loading
and the parser for queries/example_queries.sparql.

Keeping these in one place means transform.py, link.py, validate.py,
sparql_terminal.py and app.py can never disagree about a namespace or a query.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from rdflib import Graph, Namespace
from rdflib.namespace import DC, DCTERMS, FOAF, OWL, RDF, RDFS, SKOS, XSD

# ─────────────────────────────────────────────────────────────
#  Paths
# ─────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent.parent
RAW_DIR     = BASE_DIR / "data" / "raw"
RDF_DIR     = BASE_DIR / "data" / "rdf"
LINKED_DIR  = BASE_DIR / "data" / "linked"
LINKS_DIR   = BASE_DIR / "data" / "links"      # link discovery output + caches
ONTO_TTL    = BASE_DIR / "ontology" / "football.ttl"
QUERIES_FILE = BASE_DIR / "queries" / "example_queries.sparql"

RDF_TTL     = RDF_DIR / "football_data.ttl"
LINKED_TTL  = LINKED_DIR / "football_linked.ttl"
VOID_TTL    = LINKED_DIR / "void.ttl"

DISCOVERED_LINKS_CSV = LINKS_DIR / "discovered_links.csv"
DBPEDIA_CACHE_JSON   = LINKS_DIR / "dbpedia_cache.json"
ENRICHMENT_JSON      = LINKS_DIR / "dbpedia_enrichment.json"

# ─────────────────────────────────────────────────────────────
#  Namespaces
# ─────────────────────────────────────────────────────────────
BASE_URI = "http://semantic-football.org/"
ONTO   = Namespace(BASE_URI + "ontology#")
DATA   = Namespace(BASE_URI + "data/")
DATASET_URI = BASE_URI + "data"            # the dataset itself (VoID)
ONTOLOGY_URI = BASE_URI + "ontology"

SCHEMA = Namespace("https://schema.org/")
VOID   = Namespace("http://rdfs.org/ns/void#")
PROV   = Namespace("http://www.w3.org/ns/prov#")
DBO    = Namespace("http://dbpedia.org/ontology/")
DBR    = Namespace("http://dbpedia.org/resource/")
WD     = Namespace("http://www.wikidata.org/entity/")
GEONAMES = Namespace("http://sws.geonames.org/")   # canonical GeoNames URIs use http

LICENSE_URI = "https://creativecommons.org/licenses/by/4.0/"

# One prefix per entity type, so queries can write  club:arsenal  instead of
# <http://semantic-football.org/data/club/arsenal>  ("data:club/arsenal" is
# not a legal prefixed name in SPARQL because of the "/").
ENTITY_TYPES = ["club", "player", "manager", "stadium", "match", "goal", "transfer",
                "league", "season", "city", "nationality"]

PREFIXES = {
    "onto": ONTO, "data": DATA,
    **{t: Namespace(f"{DATA}{t}/") for t in ENTITY_TYPES}, "rdf": RDF, "rdfs": RDFS, "owl": OWL,
    "xsd": XSD, "schema": SCHEMA, "foaf": FOAF, "skos": SKOS, "dc": DC,
    "dcterms": DCTERMS, "void": VOID, "prov": PROV, "dbo": DBO, "dbr": DBR,
    "wd": WD, "geonames": GEONAMES,
}

# Automatically prepended to every query typed in the terminal / web UI.
PREFIX_BLOCK = "".join(f"PREFIX {p + ':':<9} <{ns}>\n" for p, ns in PREFIXES.items())

# Short forms used when displaying URIs in tables.
DISPLAY_PREFIXES = [
    (str(DATA), "data:"), (str(ONTO), "onto:"), (str(WD), "wd:"),
    (str(DBR), "dbr:"), (str(DBO), "dbo:"), (str(GEONAMES), "geonames:"),
    (str(SCHEMA), "schema:"), (str(OWL), "owl:"), (str(RDF), "rdf:"),
    (str(RDFS), "rdfs:"), (str(FOAF), "foaf:"), (str(SKOS), "skos:"),
    (str(DCTERMS), "dcterms:"), (str(DC), "dc:"), (str(VOID), "void:"),
]


def shorten(uri: str) -> str:
    for long, short in DISPLAY_PREFIXES:
        if uri.startswith(long):
            return short + uri[len(long):]
    return uri


def bind_prefixes(g: Graph) -> Graph:
    for p, ns in PREFIXES.items():
        g.bind(p, ns, override=True)
    return g


# ─────────────────────────────────────────────────────────────
#  CSV helpers
# ─────────────────────────────────────────────────────────────
def read_csv(filename: str) -> list[dict]:
    path = RAW_DIR / filename
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for i, row in enumerate(rows, start=2):
        if None in row:   # more fields than header → unquoted comma
            raise ValueError(f"{filename}:{i} has more columns than the header "
                             f"(quote values that contain commas): {row[None]}")
    return rows


# ─────────────────────────────────────────────────────────────
#  Graph loading
# ─────────────────────────────────────────────────────────────
def load_graph(dataset: str = "5star", with_ontology: bool = True) -> Graph:
    """Load ontology + 4★ or 5★ data (+ VoID description) into one graph."""
    path = LINKED_TTL if dataset == "5star" else RDF_TTL
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run scripts/transform.py"
            + (" and scripts/link.py" if dataset == "5star" else "") + " first.")
    g = bind_prefixes(Graph())
    if with_ontology and ONTO_TTL.exists():
        g.parse(ONTO_TTL, format="turtle")
    g.parse(path, format="turtle")
    if dataset == "5star" and VOID_TTL.exists():
        g.parse(VOID_TTL, format="turtle")
    return g


# ─────────────────────────────────────────────────────────────
#  Example queries (parsed from queries/example_queries.sparql)
# ─────────────────────────────────────────────────────────────
_TAG = re.compile(r"^#@(\w+):\s*(.*)$")


def load_example_queries(path: Path = QUERIES_FILE) -> dict[str, dict]:
    """
    Returns {name: {"description": str, "query": str, "requires": set[str]}}
    in file order.
    """
    queries: dict[str, dict] = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _TAG.match(line.strip())
        if m:
            key, value = m.group(1), m.group(2).strip()
            if key == "name":
                current = {"description": "", "query_lines": [], "requires": set()}
                queries[value] = current
            elif current is not None and key == "desc":
                current["description"] = value
            elif current is not None and key == "requires":
                current["requires"].update(v.strip() for v in value.split(","))
            continue
        if current is not None:
            current["query_lines"].append(line)
    for q in queries.values():
        q["query"] = "\n".join(q.pop("query_lines")).strip() + "\n"
    return queries


def query_type(sparql: str) -> str:
    """Rough detection of the query form (after PREFIX/BASE declarations)."""
    body = re.sub(r"#[^\n<>]*$", "", sparql, flags=re.M)        # strip comments
    body = re.sub(r"(?is)^\s*((PREFIX\s+\S*\s*<[^>]*>|BASE\s*<[^>]*>)\s*)*", "", body)
    m = re.match(r"\s*(SELECT|ASK|CONSTRUCT|DESCRIBE|INSERT|DELETE|LOAD|CLEAR|"
                 r"CREATE|DROP|COPY|MOVE|ADD|WITH)\b", body, re.I)
    return m.group(1).upper() if m else "UNKNOWN"


UPDATE_FORMS = {"INSERT", "DELETE", "LOAD", "CLEAR", "CREATE", "DROP",
                "COPY", "MOVE", "ADD", "WITH"}


# ─────────────────────────────────────────────────────────────
#  Curated (hand-checked) external links stored in the CSV files
# ─────────────────────────────────────────────────────────────
#  file, id column, local path prefix
CURATED_SOURCES = [
    ("clubs.csv",       "club_id",    "club"),
    ("other_clubs.csv", "club_id",    "club"),
    ("players.csv",     "player_id",  "player"),
    ("managers.csv",    "manager_id", "manager"),
    ("stadiums.csv",    "stadium_id", "stadium"),
    ("leagues.csv",     "league_id",  "league"),
    ("seasons.csv",     "season_id",  "season"),
    ("cities.csv",      "city_id",    "city"),
]


def normalize_uri(u: str) -> str:
    """Compare DBpedia IRIs regardless of percent-encoding / Unicode form."""
    import unicodedata
    from urllib.parse import unquote
    u = unquote(u.strip()).replace("https://dbpedia.org/", "http://dbpedia.org/")
    return unicodedata.normalize("NFC", u)


def curated_links() -> list[tuple[str, str]]:
    """[(local_uri, external_uri)] for every wikidata_id / dbpedia_uri / geonames_id cell."""
    links = []
    for filename, id_col, prefix in CURATED_SOURCES:
        for row in read_csv(filename):
            local = str(DATA[f"{prefix}/{row[id_col]}"])
            if row.get("wikidata_id"):
                links.append((local, str(WD[row["wikidata_id"].strip()])))
            if row.get("dbpedia_uri"):
                links.append((local, normalize_uri(row["dbpedia_uri"])))
            if row.get("geonames_id"):
                links.append((local, f"{GEONAMES}{row['geonames_id'].strip()}/"))
    return links
