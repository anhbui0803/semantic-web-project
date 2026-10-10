# Football Semantic Web — Linked Open Data Application (IT6390E, Topic 1)

> **Domain:** Football (Premier League 2023–24)

The project builds a Linked Open Data (LOD) application for the football domain and
covers all five requirements of Topic 1:

| # | Topic 1 requirement | Where it is done |
|---|---|---|
| 1 | Define an ontology for the domain | `ontology/football.ttl` |
| 2 | Collect data | `data/raw/*.csv` |
| 3 | Transform the data to the 4★ standard | `scripts/transform.py` → `data/rdf/` |
| 4 | Find and establish links to other datasets (5★) | `scripts/discover_links.py` + `scripts/link.py` → `data/links/`, `data/linked/` |
| 5 | Query interface: SPARQL endpoint / terminal | `scripts/app.py` (endpoint + web UI + resolvable URIs), `scripts/sparql_terminal.py` |

`scripts/validate.py` checks the result automatically.

---

## Quick start

```bash
pip install -r requirements.txt

python scripts/run_all.py              # full pipeline, then the SPARQL terminal
python scripts/run_all.py --web        # full pipeline, then the web UI / SPARQL endpoint
python scripts/run_all.py --offline    # do not contact DBpedia (uses data/links cache)
python scripts/run_all.py --no-serve   # pipeline + validation only
```

Or step by step:

```bash
python scripts/transform.py                 # Step 3 → data/rdf/football_data.ttl  (4★)
python scripts/discover_links.py --enrich   # Step 4a → data/links/  (needs internet)
python scripts/link.py                      # Step 4b → data/linked/football_linked.ttl + void.ttl (5★)
python scripts/validate.py                  # quality checks
python scripts/sparql_terminal.py           # Step 5 — terminal
python scripts/app.py                       # Step 5 — http://127.0.0.1:5000
```

---

## Project structure

```
semantic-web/
├── ontology/football.ttl          ← OWL ontology, aligned with schema.org / FOAF / DBpedia ontology
├── data/
│   ├── raw/                       ← collected data (CSV)
│   │   ├── clubs.csv  other_clubs.csv  players.csv  managers.csv  stadiums.csv
│   │   ├── matches.csv  goals.csv  transfers.csv  leagues.csv  seasons.csv
│   │   └── cities.csv  nationalities.csv
│   ├── rdf/                       ← 4★ output (no external links)
│   ├── links/                     ← link discovery: discovered_links.csv, evaluation.md, caches
│   └── linked/                    ← 5★ output + void.ttl
├── render.yaml                    ← Render deployment (free web service)
├── queries/example_queries.sparql ← all example queries (single source for terminal, web UI, validation)
└── scripts/
    ├── common.py                  ← namespaces, prefixes, paths, helpers shared by all scripts
    ├── transform.py               ← Step 3
    ├── discover_links.py          ← Step 4a — automatic link discovery + evaluation
    ├── link.py                    ← Step 4b — links, enrichment, VoID
    ├── validate.py                ← quality checks
    ├── sparql_terminal.py         ← Step 5 — terminal
    ├── app.py  templates/         ← Step 5 — SPARQL endpoint, web UI, Linked Data pages
    └── run_all.py                 ← whole pipeline
```

---

## Step 1 — Ontology (`ontology/football.ttl`)

OWL 2 ontology in Turtle, namespace `onto: <http://semantic-football.org/ontology#>`.

**Classes:** `FootballClub`, `Player`, `Manager`, `Match`, `League`, `Season`, `Stadium`,
`Position`, `Goal`, `Transfer`, `Nationality`, `City`.

**Re-use of existing vocabularies** (important for 5★ — links at the schema level):

| Our term | Aligned with |
|---|---|
| `onto:FootballClub` | `schema:SportsTeam`, `dbo:SoccerClub` |
| `onto:Player` / `onto:Manager` | `foaf:Person`, `schema:Person`, `dbo:SoccerPlayer` / `dbo:SoccerManager` |
| `onto:Match`, `onto:League`, `onto:Season`, `onto:Stadium` | `schema:SportsEvent`, `dbo:FootballMatch`, `dbo:SoccerLeague`, `dbo:SoccerLeagueSeason`, `dbo:Stadium` |
| `onto:playsFor`, `onto:managedBy`, `onto:hasStadium` | `rdfs:subPropertyOf` `dbo:team`, `dbo:manager`, `dbo:ground` |
| `onto:dateOfBirth`, `onto:capacity`, `onto:name` | `dbo:birthDate`, `dbo:seatingCapacity`, `schema:name` |

Every object property has an `rdfs:domain`/`rdfs:range` and every datatype property an XSD
range; `validate.py` checks that the data respects them. The ontology is published under
CC BY 4.0 (`dcterms:license`).

## Step 2 — Data (`data/raw/`)

CSV files collected by hand from public sources: fifteen real 2023–24 Premier League
matches, one for every pair of the six clubs, with all 45 goals (scorer, minute, added time,
penalty, own goal). `players.csv` contains exactly the 31 players who scored in those
matches. Further files: 6 clubs (+7 clubs that appear only in transfers), 6 managers,
6 stadiums, 10 transfers, 4 seasons, 9 cities, 23 nationalities.

Every file's source (openfootball, 11v11.com match pages, Wikipedia, Wikidata, GeoNames) is
listed in [`data/raw/SOURCES.md`](data/raw/SOURCES.md); values that could not be confirmed
are left empty rather than guessed. The CSVs also hold the *curated* external identifiers
(`wikidata_id`, `dbpedia_uri`, `geonames_id`) that serve as the gold standard for link discovery.

## Step 3 — 4★ transformation (`scripts/transform.py`)

* every entity gets an HTTP URI: `http://semantic-football.org/data/<type>/<id>`
  (e.g. `…/data/player/erling_haaland`, `…/data/match/m001`)
* foreign keys become RDF links between our own resources (`onto:playsFor`, `onto:homeTeam`, …)
* typed literals (`xsd:date`, `xsd:integer`, `xsd:decimal`, `xsd:boolean`)
* `rdfs:label` on every entity so generic Linked Data tools can display it
* derived facts, e.g. `onto:wonBy` computed from the score

The 4★ output deliberately contains **no** links to other datasets: compare
`data/rdf/` (4★) with `data/linked/` (5★) to see exactly what the fifth star adds.

## Step 4 — 5★ links

### 4a. Automatic link discovery (`scripts/discover_links.py`)

A small Silk-style linkage rule:

1. **Candidates** – DBpedia Lookup search by name, restricted to the matching DBpedia class.
2. **Details** – one batched SPARQL query to `https://dbpedia.org/sparql` for types,
   English label, `dbo:birthDate` and the `owl:sameAs` link to Wikidata.
3. **Score** – `0.6·name similarity + 0.4·birth-date match` for people, name similarity
   otherwise (accent-insensitive; "F.C.", "(footballer)" … removed). A different birth date
   rejects the candidate.
4. **Decision** – ≥ 0.90 accepted, ≥ 0.75 review; two candidates within 0.05 → review (ambiguous).
5. **Evaluation** – accepted links are compared with the curated links:
   precision / recall / F1, plus a check that the curated Wikidata IDs agree with DBpedia.
   Report: `data/links/evaluation.md`.

All HTTP responses are cached in `data/links/dbpedia_cache.json`, so the result is
reproducible and later runs work offline (`--offline`). `--enrich` additionally caches
DBpedia abstracts and thumbnails.

### 4b. Writing the links (`scripts/link.py`)

| Link | Target | Notes |
|---|---|---|
| `owl:sameAs` | DBpedia, Wikidata | clubs, players, managers, stadiums, league, seasons, cities (curated + accepted discovered links; curated links win on conflict) |
| `owl:sameAs` | GeoNames | cities (`http://sws.geonames.org/<id>/`) |
| `onto:nationalityCountry` | Wikidata / DBpedia countries | a nationality is not a country, so `owl:sameAs` would be wrong |
| `rdfs:comment`, `foaf:depiction` | from DBpedia | only if the enrichment cache exists |

`link.py` also writes **`data/linked/void.ttl`**, a [VoID](https://www.w3.org/TR/void/)
description with the licence, SPARQL endpoint, data dumps, statistics and one `void:Linkset`
per external dataset — the metadata the [LOD Cloud](https://lod-cloud.net/) asks for
(≥ 1000 triples and ≥ 50 links to an existing LOD dataset; this dataset has 70 to DBpedia).

| ★ | Criterion | How it is met |
|---|---|---|
| ★ | On the web, open licence | served by `app.py`; CC BY 4.0 in VoID and ontology |
| ★★ | Structured data | RDF |
| ★★★ | Non-proprietary format | Turtle, N-Triples, RDF/XML, JSON-LD |
| ★★★★ | URIs to denote things | HTTP URIs that resolve (`/data/...`, content negotiation) |
| ★★★★★ | Linked to other data | `owl:sameAs` → DBpedia, Wikidata, GeoNames; schema-level alignment to dbo:/schema.org |

## Step 5 — Query interfaces

### SPARQL endpoint + web UI (`scripts/app.py`)

```bash
python scripts/app.py          # http://127.0.0.1:5000
```

| URL | What |
|---|---|
| `/` | Web UI with all example queries |
| `/sparql` | **SPARQL 1.1 Protocol** endpoint (read-only) |
| `/data/<type>/<id>` | Linked Data page of an entity: HTML for browsers, RDF for machines |
| `/ontology` | the ontology (HTML / RDF) |
| `/void` | VoID description |
| `/dump/football_linked.ttl` | data dump |

```bash
# SELECT → SPARQL JSON (default), XML or CSV
curl -G --data-urlencode "query=SELECT ?name WHERE { ?c a onto:FootballClub ; onto:name ?name }" http://127.0.0.1:5000/sparql
curl -H "Accept: text/csv" --data-urlencode "query=SELECT ?n WHERE { ?p a onto:Player ; onto:name ?n }" http://127.0.0.1:5000/sparql
curl -H "Content-Type: application/sparql-query" --data "ASK { club:arsenal a onto:FootballClub }" http://127.0.0.1:5000/sparql
# CONSTRUCT / DESCRIBE → Turtle (default), N-Triples, RDF/XML, JSON-LD
curl -H "Accept: application/ld+json" "http://127.0.0.1:5000/sparql?query=DESCRIBE%20player:erling_haaland"
# Dereference a URI with content negotiation
curl -H "Accept: text/turtle" http://127.0.0.1:5000/data/club/arsenal
```

The endpoint works with any SPARQL client (YASGUI, SPARQLWrapper, Protégé, …).
Update requests are rejected (HTTP 403).

**About the URIs.** Entity URIs use `http://semantic-football.org/…`. Running locally,
`http://127.0.0.1:5000/data/club/arsenal` serves `http://semantic-football.org/data/club/arsenal`.
To make the URIs themselves resolvable, deploy `app.py` under that domain, or register a
permanent [w3id.org](https://w3id.org) redirect and change `BASE_URI` in `scripts/common.py`.

### Deploying on Render (free)

`render.yaml` describes the web service, so the repository can be deployed as a Render
Blueprint:

1. Push the repository to GitHub (the RDF files in `data/rdf/` and `data/linked/` must be committed).
2. On [render.com](https://render.com) choose **New → Blueprint**, connect the GitHub repository
   and confirm. Render installs `requirements.txt` and starts
   `gunicorn --chdir scripts app:app`.
3. The service is then available at `https://<service-name>.onrender.com`
   (`/`, `/sparql`, `/data/club/arsenal`, `/void`, `/health`).

The VoID description automatically uses the public address (Render's `RENDER_EXTERNAL_URL`,
or `FOOTBALL_PUBLIC_BASE` if set). Entity URIs stay `http://semantic-football.org/...`;
on Render they are served under `https://<service-name>.onrender.com/data/...`.
The free plan stops the service after 15 minutes without traffic; the next request takes
about a minute while it starts again, so open the site shortly before a demo.

*Alternative:* the dump can also be loaded into Apache Jena Fuseki
(`fuseki-server --file=data/linked/football_linked.ttl /football`), which then offers an
endpoint at `http://localhost:3030/football/sparql`.

### SPARQL terminal (`scripts/sparql_terminal.py`)

```bash
python scripts/sparql_terminal.py                     # REPL on the local 5★ dataset
python scripts/sparql_terminal.py --dataset 4star     # 4★ dataset (no links)
python scripts/sparql_terminal.py -r standings        # run one example and exit
python scripts/sparql_terminal.py -q "DESCRIBE player:erling_haaland"
python scripts/sparql_terminal.py -f my_query.rq --format csv
python scripts/sparql_terminal.py --endpoint http://127.0.0.1:5000/sparql   # talk to the endpoint
```

| Command | Description |
|---|---|
| `:examples` | list the named example queries |
| `:run <name>` / `:show <name>` | run / print an example |
| `:file <path>` | run a query from a file |
| `:format table\|csv\|json` | output format |
| `:prefixes`, `:count`, `:help`, `:quit` | |

Prefixes are added automatically, including one per entity type, so queries can write
`club:arsenal`, `player:erling_haaland`, `league:premier_league`.

### Example queries (`queries/example_queries.sparql`)

23 queries covering SELECT, ASK, CONSTRUCT, DESCRIBE, aggregation (`standings` computes the
league table), property paths, and link exploitation (`players_by_country`, `stadiums`,
`linksets`). `federated_dbpedia_abstract` uses `SERVICE <https://dbpedia.org/sparql>` to follow
our `owl:sameAs` links into live DBpedia (needs internet).

---

## Validation (`scripts/validate.py`)

Checks that all ontology terms are declared, every entity has a type and label, no reference
is dangling, domains/ranges/datatypes are respected, goals add up to scores, links are
well-formed with at most one per entity and dataset, the VoID file is present, and every
example query returns a result.

Current status: 0 errors, 0 warnings. The goal records of all 15 matches add up to the
official scores.

## External datasets

| Dataset | URL |
|---|---|
| DBpedia | https://dbpedia.org |
| Wikidata | https://www.wikidata.org |
| GeoNames | https://www.geonames.org |
| Schema.org | https://schema.org |
