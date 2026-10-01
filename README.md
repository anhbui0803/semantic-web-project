# European Football Linked Open Data (IT6390E – Capstone Topic 1)

A 5-star Linked Open Data application for the **football** domain. It covers every match, club and
final standing of the top-5 European leagues (Premier League, La Liga, Bundesliga, Serie A,
Ligue 1) in season 2023/24. The data is published as RDF, linked to DBpedia, Wikidata and GeoNames,
validated with SHACL and an OWL 2 reasoner, and can be queried through a SPARQL endpoint, a Linked
Data browser and a terminal.

| | |
|---|---|
| Domain | Football: 5 leagues, 96 clubs, 1,752 matches, 96 final standings, 6 countries |
| Data source | [openfootball/football.json](https://github.com/openfootball/football.json) (public domain) |
| Size | ≈24,500 triples (data + links + ontology + VoID) |
| External links | 321 link triples: 107 → DBpedia, 107 → Wikidata, 6 → GeoNames (all `owl:sameAs`), plus 101 `rdfs:seeAlso` → Wikipedia |
| Stack | Python 3.9+, rdflib (RDF / SPARQL 1.1), Flask (HTTP), pyshacl, owlready2 + HermiT |

## Project files

```
ontology.ttl   (1) OWL 2 DL ontology: classes, properties, restrictions, schema.org / DBpedia alignment
build.py       (2)(3)(4) collect → transform to RDF → link (Silk-style) → VoID; plus --check
shapes.ttl     SHACL shapes: data-quality rules checked by `python build.py --check`
silk.xml       (4) Silk Link Specification that reproduces the club linking with the Silk framework
server.py      (5) SPARQL 1.1 endpoint + Linked Data front-end + terminal client
data/raw/*.json        collected source data (one file per league)
data/football.ttl      the 4-star RDF dataset
data/links.ttl         the 5-star linkset (owl:sameAs)
data/void.ttl          VoID / DCAT dataset description (what lod-cloud.net asks for)
```

## Run

```bash
pip install -r requirements.txt
python build.py            # rebuild data/*.ttl (the generated files are already committed)
python build.py --check    # SHACL validation + OWL 2 consistency check (HermiT needs Java)
python server.py           # http://localhost:5000 (UI), endpoint at /sparql
python server.py shell     # SPARQL terminal (:examples, :1 … :8, end a query with ';')
python server.py query "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
```

Options that need Internet access:
- `python build.py --download` downloads the raw JSON again.
- `python build.py --online` discovers club links live with DBpedia Lookup, follows DBpedia
  redirects and reads each club's Wikidata ID from the DBpedia SPARQL endpoint.
- `python build.py --check --online` re-verifies every published DBpedia/Wikidata link on the live
  endpoint.

To publish under another base URI, set `LOD_BASE=http://myhost:8080/` before running both
`build.py` and `server.py`.

## How each requirement is met

### 1. Ontology (`ontology.ttl`)
- Namespace `fb:` = `http://localhost:5000/ontology#`. Labels are in English and Vietnamese.
- **Classes:** `fb:Country`, `fb:League`, `fb:Season`, `fb:Club`, `fb:Match`, `fb:Standing`, and
  `fb:MatchResult`, which is an enumeration: `owl:oneOf (fb:HomeWin fb:Draw fb:AwayWin)`. All
  classes are declared pairwise disjoint.
- **Object properties:** `country`, `ofLeague` (with inverse `hasSeason`), `inSeason`,
  `participant`, `champion`, `competitor ⊒ {homeTeam, awayTeam, winner}`, `result`, `team`.
  `homeTeam` and `awayTeam` are functional and declared property-disjoint.
- **Datatype properties:** `name`, `seasonLabel`, `kickOff` (`xsd:dateTime`), `round`,
  `home/awayScore`, `home/awayHalfTimeScore`, `rank`, `played`, `won`, `drawn`, `lost`,
  `goalsFor`, `goalsAgainst`, `goalDifference`, `points`. Only datatypes from the OWL 2 datatype
  map are used, so Protégé and HermiT accept the ontology.
- **Restrictions:** every `fb:Match` has exactly one home team and one away team
  (`owl:qualifiedCardinality 1`), and every `fb:Club` takes part in some `fb:Season`.
- **Re-use of existing vocabularies:**
  - `rdfs:subClassOf` schema:SportsTeam, SportsEvent, …
  - `owl:equivalentClass` dbo:SoccerClub, SoccerLeague, FootballMatch, Country
  - `rdfs:subPropertyOf` schema:homeTeam, awayTeam, competitor, startDate, name
- **Verified:** HermiT reports the ontology together with all the data as consistent, with no
  unsatisfiable class.

### 2. Data collection (`build.py`, collect step)
Downloads the 2023/24 fixtures and results of the five leagues (JSON, one file per league) into
`data/raw/`. Every club plays 2 × (n − 1) matches, and `--check` confirms this. Final league tables
are not part of the source. They are **derived** during the transform:
- 3 points for a win;
- ranking by points, then goal difference, then goals scored;
- administrative point deductions are not modelled.

The derived champions and points match the official tables: Man City 91, Real Madrid 95,
Leverkusen 90, Inter 94, PSG 76.

### 3. 4-star data (`build.py`, transform step)
- Every entity has an HTTP URI under `http://localhost:5000/resource/`, for example:
  - `res:club/arsenal-fc`
  - `res:league/premier-league`
  - `res:season/premier-league-2023-24`
  - `res:match/premier-league-2023-24/2023-08-12-arsenal-fc-vs-nottingham-forest-fc`
  - `res:standing/premier-league-2023-24/manchester-city-fc`
- Literals are typed (`xsd:dateTime`, `xsd:nonNegativeInteger`, …) and names are language-tagged.
- The URIs **dereference** using the W3C *Cool URIs for the Semantic Web* 303 pattern.
  `GET /resource/…` answers `303 See Other` with `Vary: Accept`. It sends browsers to `/page/…`
  (HTML) and RDF clients to `/data/….ttl|rdf|jsonld|nt`. Each RDF document also describes itself
  with `foaf:primaryTopic`, a licence and `void:inDataset`.

  ```bash
  curl -I -H "Accept: text/turtle" http://localhost:5000/resource/club/arsenal-fc
  curl -L -H "Accept: text/turtle" http://localhost:5000/resource/club/arsenal-fc
  ```

### 4. 5-star links (`build.py`, link step; `silk.xml`)
| Entities | Target datasets | Method |
|---|---|---|
| 96 clubs | DBpedia (`dbo:SoccerClub`) + Wikidata | Silk-style link discovery |
| 5 leagues | DBpedia, Wikidata | fixed mapping |
| 6 countries (incl. Monaco for AS Monaco) | DBpedia, Wikidata, GeoNames | fixed mapping |

The club linkage rule mirrors a Silk specification:
- **Blocking:** candidates are compared only within the league's country.
- **Normalisation:** accents, dots, case and generic tokens such as *FC, CF, AC, Calcio* are removed.
- **Confidence:** the average of a Levenshtein ratio and a token-Jaccard similarity, taking the best
  score over all labels of the candidate.
- **Acceptance:** the best candidate is accepted when its confidence is ≥ 0.75.

The build prints every decision with its score and the runner-up score. The smallest margin between
a winner and its runner-up is 0.26. The candidate pool includes deliberate distractors so the
matcher has to discriminate, for example Manchester City/United, Real Madrid / Real Madrid
Castilla, Inter / AC Milan, and reserve and women's teams.

**Link verification (2026-10-01).** Every link target was checked on the live
`https://dbpedia.org/sparql` and `https://query.wikidata.org/sparql`:
- each DBpedia resource is a `dbo:SoccerClub` / `SoccerLeague` / `Country`;
- its `owl:sameAs` points to the same Wikidata item that we publish (107/107 pairs agree);
- each Wikidata item matches the club's English Wikipedia article, and Wikidata P1566 confirms the
  GeoNames IDs.

DBpedia still uses some older article titles, such as `A.C._Milan`, `S.S.C._Napoli` and
`Juventus_F.C.`. The newer titles exist in DBpedia only as redirect stubs, so the links point to the
resource that holds the data. The verified pool is embedded in `build.py` so the build also works
offline. `--online` recomputes the links from the live services, and `--check --online` re-runs the
verification.

`silk.xml` runs the same rule with the Silk framework against `https://dbpedia.org/sparql`.

The dataset is described with **VoID**:
- `void:Dataset` with triple and entity counts and class partitions;
- one `void:Linkset` per target (DBpedia 107, Wikidata 107, GeoNames 6);
- SPARQL endpoint, data dump, example resource, licence, and the formats offered.

It is served at `/void` and `/.well-known/void`, which is the description lod-cloud.net asks for
when registering a dataset.

### 5. SPARQL endpoint and terminal (`server.py`)
- **Endpoint:** `GET` or `POST /sparql` following the SPARQL 1.1 Protocol, using a `query=`
  parameter or an `application/sparql-query` body.
  - SELECT and ASK results come as JSON, XML, CSV or TSV.
  - CONSTRUCT and DESCRIBE results come as Turtle, RDF/XML, JSON-LD or N-Triples.
  - The format is chosen by the `Accept` header or a `format=` parameter.
  - The endpoint is read-only (updates are rejected) and has CORS enabled.
- **Web UI and browser:** the UI at `/` has 8 example queries and clickable results. There is also
  a Linked Data browser at `/page/…`, plus `/ontology`, `/void` and `/dump.ttl`.
- **Terminal:** `python server.py shell` (interactive) or `python server.py query …` (one query).

```bash
curl "http://localhost:5000/sparql" -H "Accept: text/csv" \
     --data-urlencode "query=PREFIX fb: <http://localhost:5000/ontology#>
SELECT ?club ?points WHERE { ?s fb:champion ?c . ?c fb:name ?club .
  ?st fb:team ?c ; fb:inSeason ?s ; fb:points ?points }"
```

Example queries shipped with the UI and terminal:
- champions of every league;
- the Premier League table;
- biggest wins;
- home/draw/away ratio and goals per match for each league;
- half-time comebacks;
- club → DBpedia/Wikidata links;
- the class hierarchy of the ontology;
- a **federated query** (needs Internet) that follows `owl:sameAs` into DBpedia with
  `SERVICE <https://dbpedia.org/sparql>` and returns each champion's stadium. Its inner pattern was
  tested on the live endpoint and returns Parc des Princes, BayArena, City of Manchester Stadium,
  San Siro and Santiago Bernabéu.

## Quality assurance (`python build.py --check`)
| Check | Tool | What it guarantees |
|---|---|---|
| OWL 2 DL consistency | HermiT (owlready2) | ontology + data has a model; no unsatisfiable class |
| Data shapes | SHACL (`shapes.ttl`, pyshacl) | every club/match/standing/season is complete and well-typed; every club is linked |
| Business rules | SHACL-SPARQL | half-time ≤ full-time; result/winner agree with the score; W+D+L = played; points = 3W+D; GD = GF−GA; double round-robin |
| Link validity | `--check --online` | every DBpedia target exists and agrees with the published Wikidata ID |

The rules were tested by injecting faults: a wrong score, wrong points, a missing link, a missing
team and a club playing itself. Each fault was reported.

## 5-star checklist ([5stardata.info](https://5stardata.info/en/))

| ★ | Requirement | Here |
|---|---|---|
| ★ | on the web, open licence | CC0 (the source is public domain), `dcterms:license` in VoID and on every RDF document |
| ★★ | machine-readable structured data | JSON source, RDF output |
| ★★★ | non-proprietary format | Turtle, RDF/XML, JSON-LD, N-Triples, SPARQL results |
| ★★★★ | URIs to denote things | HTTP URIs for every entity, dereferenceable with content negotiation (303) |
| ★★★★★ | link to other data | `owl:sameAs` to DBpedia, Wikidata and GeoNames, verified live and described as `void:Linkset` |
