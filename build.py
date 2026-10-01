"""
build.py - Linked Open Data pipeline for European football (Topic 1)

  Step 1  collect   : download raw match data (openfootball, public domain)  -> data/raw/*.json
  Step 2  transform : JSON -> RDF using ontology.ttl (4-star: URIs + RDF)  -> data/football.ttl
  Step 3  link      : Silk-style link discovery to DBpedia / Wikidata /
                      GeoNames (5-star: links to other LOD datasets)          -> data/links.ttl
  Step 4  describe  : VoID + DCAT dataset metadata (for the LOD cloud)       -> data/void.ttl

Usage:
  python build.py              # full pipeline, offline-safe (uses cached raw data + DBpedia snapshot)
  python build.py --download   # re-download raw data from GitHub first
  python build.py --online     # discover links live against DBpedia Lookup + SPARQL endpoint
  python build.py --check      # quality checks: SHACL shapes + OWL 2 reasoner (HermiT)
  python build.py --check --online   # ... + re-verify every DBpedia/Wikidata link on the live endpoint
"""
import argparse, difflib, json, os, re, sys, unicodedata, urllib.parse, urllib.request
from collections import defaultdict
from rdflib import Graph, Literal, Namespace, URIRef, BNode
from rdflib.namespace import RDF, RDFS, OWL, XSD, DCTERMS, FOAF

BASE = os.environ.get("LOD_BASE", "http://localhost:5000/")
FB = Namespace(BASE + "ontology#")
RES = Namespace(BASE + "resource/")
DBR = Namespace("http://dbpedia.org/resource/")
WD = Namespace("http://www.wikidata.org/entity/")
SCHEMA = Namespace("https://schema.org/")
VOID = Namespace("http://rdfs.org/ns/void#")
DCAT = Namespace("http://www.w3.org/ns/dcat#")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw")
SOURCE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master/{season}/{code}.json"
SEASON = "2023-24"

# League metadata: code -> (slug, name, country slug, country name, DBpedia league, Wikidata league)
LEAGUES = {
    "en.1": ("premier-league", "Premier League", "england", "England", "Premier_League", "Q9448"),
    "es.1": ("la-liga", "La Liga", "spain", "Spain", "La_Liga", "Q324867"),
    "de.1": ("bundesliga", "Bundesliga", "germany", "Germany", "Bundesliga", "Q82595"),
    "it.1": ("serie-a", "Serie A", "italy", "Italy", "Serie_A", "Q15804"),
    "fr.1": ("ligue-1", "Ligue 1", "france", "France", "Ligue_1", "Q13394"),
}
# country slug -> (name, DBpedia, Wikidata, GeoNames id)   [verified 2026-10-01 on DBpedia + Wikidata SPARQL]
COUNTRIES = {
    "england": ("England", "England", "Q21", "6269131"),
    "spain": ("Spain", "Spain", "Q29", "2510769"),
    "germany": ("Germany", "Germany", "Q183", "2921044"),
    "italy": ("Italy", "Italy", "Q38", "3175395"),
    "france": ("France", "France", "Q142", "3017382"),
    "monaco": ("Monaco", "Monaco", "Q235", "2993457"),
}
# Clubs whose country differs from the country of their league
CLUB_COUNTRY = {"AS Monaco FC": "monaco"}

# ---------------------------------------------------------------------------
# DBpedia candidate pool used by the link step when DBpedia is not reachable.
#   (DBpedia resource, labels separated by "|", Wikidata QID)
# Verified on 2026-10-01: every target club below was queried on
# https://dbpedia.org/sparql (rdf:type dbo:SoccerClub, not a wikiPageRedirects
# stub, owl:sameAs -> the QID) and cross-checked on https://query.wikidata.org
# (QID <-> English Wikipedia article). Note that DBpedia still uses some older
# article titles (e.g. A.C._Milan, S.S.C._Napoli) - the resource that carries the
# data is the one we link to. Rows with an empty QID are "distractors" (reserve,
# women's or rival clubs) that the matcher has to reject, as Silk would on live data.
# Refresh with:  python build.py --online
# ---------------------------------------------------------------------------
DBPEDIA_SNAPSHOT = {
    "england": [
        ("AFC_Bournemouth", "AFC Bournemouth|A.F.C. Bournemouth", "Q19568"),
        ("Arsenal_F.C.", "Arsenal F.C.", "Q9617"), ("Arsenal_W.F.C.", "Arsenal W.F.C.", ""),
        ("Aston_Villa_F.C.", "Aston Villa F.C.", "Q18711"), ("Brentford_F.C.", "Brentford F.C.", "Q19571"),
        ("Brighton_&_Hove_Albion_F.C.", "Brighton & Hove Albion F.C.", "Q19453"),
        ("Burnley_F.C.", "Burnley F.C.", "Q19458"), ("Chelsea_F.C.", "Chelsea F.C.", "Q9616"),
        ("Chelsea_F.C._Women", "Chelsea F.C. Women", ""), ("Crystal_Palace_F.C.", "Crystal Palace F.C.", "Q19467"),
        ("Everton_F.C.", "Everton F.C.", "Q5794"), ("Fulham_F.C.", "Fulham F.C.", "Q18708"),
        ("Liverpool_F.C.", "Liverpool F.C.", "Q1130849"), ("Luton_Town_F.C.", "Luton Town F.C.", "Q18520"),
        ("Manchester_City_F.C.", "Manchester City F.C.", "Q50602"),
        ("Manchester_United_F.C.", "Manchester United F.C.", "Q18656"),
        ("Newcastle_United_F.C.", "Newcastle United F.C.", "Q18716"),
        ("Nottingham_Forest_F.C.", "Nottingham Forest F.C.", "Q19490"), ("Notts_County_F.C.", "Notts County F.C.", ""),
        ("Sheffield_United_F.C.", "Sheffield United F.C.", "Q19607"),
        ("Sheffield_Wednesday_F.C.", "Sheffield Wednesday F.C.", ""),
        ("Tottenham_Hotspur_F.C.", "Tottenham Hotspur F.C.", "Q18741"),
        ("West_Ham_United_F.C.", "West Ham United F.C.", "Q18747"),
        ("West_Bromwich_Albion_F.C.", "West Bromwich Albion F.C.", ""),
        ("Wolverhampton_Wanderers_F.C.", "Wolverhampton Wanderers F.C.", "Q19500"),
        ("Leicester_City_F.C.", "Leicester City F.C.", ""), ("Leeds_United_F.C.", "Leeds United F.C.", ""),
        ("Southampton_F.C.", "Southampton F.C.", ""), ("Ipswich_Town_F.C.", "Ipswich Town F.C.", ""),
    ],
    "spain": [
        ("Athletic_Bilbao", "Athletic Bilbao|Athletic Club", "Q8687"), ("CA_Osasuna", "CA Osasuna", "Q10286"),
        ("Atlético_Madrid", "Atlético Madrid|Club Atlético de Madrid", "Q8701"), ("Cádiz_CF", "Cádiz CF", "Q460448"),
        ("Deportivo_Alavés", "Deportivo Alavés", "Q223620"), ("Deportivo_de_La_Coruña", "Deportivo de La Coruña", ""),
        ("FC_Barcelona", "FC Barcelona", "Q7156"), ("FC_Barcelona_Atlètic", "FC Barcelona Atlètic", ""),
        ("Getafe_CF", "Getafe CF", "Q8806"), ("Girona_FC", "Girona FC", "Q11945"), ("Granada_CF", "Granada CF", "Q8812"),
        ("RC_Celta_de_Vigo", "RC Celta de Vigo", "Q8749"), ("RCD_Mallorca", "RCD Mallorca", "Q8835"),
        ("RCD_Espanyol", "RCD Espanyol", ""), ("Rayo_Vallecano", "Rayo Vallecano|Rayo Vallecano de Madrid", "Q10300"),
        ("Real_Betis", "Real Betis|Real Betis Balompié", "Q8723"), ("Real_Madrid_CF", "Real Madrid CF", "Q8682"),
        ("Real_Madrid_Castilla", "Real Madrid Castilla", ""),
        ("Real_Sociedad", "Real Sociedad|Real Sociedad de Fútbol", "Q10315"), ("Sevilla_FC", "Sevilla FC", "Q10329"),
        ("UD_Almería", "UD Almería", "Q10407"), ("UD_Las_Palmas", "UD Las Palmas", "Q11979"),
        ("Valencia_CF", "Valencia CF", "Q10333"), ("Villarreal_CF", "Villarreal CF", "Q12297"),
        ("Real_Valladolid", "Real Valladolid", ""),
    ],
    "germany": [
        ("1._FC_Heidenheim", "1. FC Heidenheim|1. FC Heidenheim 1846", "Q162251"), ("1._FC_Köln", "1. FC Köln", "Q104770"),
        ("1._FC_Union_Berlin", "1. FC Union Berlin", "Q141971"), ("1._FSV_Mainz_05", "1. FSV Mainz 05", "Q105254"),
        ("1._FC_Kaiserslautern", "1. FC Kaiserslautern", ""), ("Bayer_04_Leverkusen", "Bayer 04 Leverkusen", "Q104761"),
        ("Borussia_Dortmund", "Borussia Dortmund", "Q41420"),
        ("Borussia_Mönchengladbach", "Borussia Mönchengladbach", "Q101959"),
        ("Eintracht_Frankfurt", "Eintracht Frankfurt", "Q38245"), ("FC_Augsburg", "FC Augsburg", "Q15755"),
        ("FC_Bayern_Munich", "FC Bayern Munich|FC Bayern München", "Q15789"),
        ("FC_Bayern_Munich_II", "FC Bayern Munich II", ""), ("RB_Leipzig", "RB Leipzig", "Q702455"),
        ("SC_Freiburg", "SC Freiburg", "Q106394"), ("SV_Darmstadt_98", "SV Darmstadt 98", "Q479351"),
        ("SV_Werder_Bremen", "SV Werder Bremen", "Q51976"),
        ("TSG_1899_Hoffenheim", "TSG 1899 Hoffenheim|TSG Hoffenheim", "Q22707"),
        ("VfB_Stuttgart", "VfB Stuttgart", "Q4512"), ("VfL_Bochum", "VfL Bochum|VfL Bochum 1848", "Q105861"),
        ("VfL_Wolfsburg", "VfL Wolfsburg", "Q101859"), ("Hertha_BSC", "Hertha BSC", ""),
        ("FC_Schalke_04", "FC Schalke 04", ""), ("Hamburger_SV", "Hamburger SV", ""),
    ],
    "italy": [
        ("A.C._Milan", "A.C. Milan|AC Milan", "Q1543"), ("A.C._Monza", "A.C. Monza|AC Monza", "Q289482"),
        ("ACF_Fiorentina", "ACF Fiorentina", "Q2052"), ("A.S._Roma", "A.S. Roma|AS Roma", "Q2739"),
        ("Atalanta_B.C.", "Atalanta B.C.|Atalanta BC", "Q1886"), ("Bologna_F.C._1909", "Bologna F.C. 1909", "Q1893"),
        ("Cagliari_Calcio", "Cagliari Calcio", "Q1900"), ("Empoli_F.C.", "Empoli F.C.", "Q6703"),
        ("Inter_Milan", "Inter Milan|FC Internazionale Milano", "Q631"), ("Frosinone_Calcio", "Frosinone Calcio", "Q845043"),
        ("Genoa_C.F.C.", "Genoa C.F.C.", "Q2074"), ("Hellas_Verona_F.C.", "Hellas Verona F.C.", "Q8639"),
        ("Juventus_F.C.", "Juventus F.C.", "Q1422"), ("Juventus_Next_Gen", "Juventus Next Gen", ""),
        ("S.S._Lazio", "S.S. Lazio|SS Lazio", "Q2609"), ("S.S.C._Napoli", "S.S.C. Napoli|SSC Napoli", "Q2641"),
        ("Torino_F.C.", "Torino F.C.", "Q2768"), ("U.S._Lecce", "U.S. Lecce", "Q13391"),
        ("U.S._Salernitana_1919", "U.S. Salernitana 1919", "Q847587"),
        ("U.S._Sassuolo_Calcio", "U.S. Sassuolo Calcio", "Q8603"), ("Udinese_Calcio", "Udinese Calcio", "Q2798"),
        ("UC_Sampdoria", "UC Sampdoria", ""), ("Parma_Calcio_1913", "Parma Calcio 1913", ""),
    ],
    "france": [
        ("AS_Monaco_FC", "AS Monaco FC", "Q180305"), ("Clermont_Foot", "Clermont Foot 63", "Q870182"),
        ("FC_Lorient", "FC Lorient", "Q48911"), ("FC_Metz", "FC Metz", "Q221525"), ("FC_Nantes", "FC Nantes", "Q192071"),
        ("Le_Havre_AC", "Le Havre AC", "Q328658"), ("Lille_OSC", "Lille OSC", "Q19516"),
        ("Montpellier_HSC", "Montpellier HSC", "Q19513"), ("OGC_Nice", "OGC Nice", "Q185163"),
        ("Olympique_Lyonnais", "Olympique Lyonnais", "Q704"), ("Olympique_Lyonnais_Féminin", "Olympique Lyonnais Féminin", ""),
        ("Olympique_de_Marseille", "Olympique de Marseille", "Q132885"),
        ("Paris_Saint-Germain_F.C.", "Paris Saint-Germain F.C.|Paris Saint-Germain FC", "Q483020"),
        ("Paris_FC", "Paris FC", ""), ("RC_Strasbourg_Alsace", "RC Strasbourg Alsace", "Q126334"),
        ("RC_Lens", "RC Lens|Racing Club de Lens", "Q191843"), ("Stade_Brestois_29", "Stade Brestois 29", "Q218372"),
        ("Stade_Rennais_F.C.", "Stade Rennais F.C.|Stade Rennais FC", "Q19509"),
        ("Stade_de_Reims", "Stade de Reims", "Q208228"), ("Toulouse_FC", "Toulouse FC", "Q19518"),
        ("FC_Girondins_de_Bordeaux", "FC Girondins de Bordeaux", ""), ("AS_Saint-Étienne", "AS Saint-Étienne", ""),
    ],
}


def slug(text):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def log(*a):
    print("[build]", *a, flush=True)


# =========================== STEP 1: COLLECT ==============================
def collect(force=False):
    os.makedirs(RAW, exist_ok=True)
    for code in LEAGUES:
        path = os.path.join(RAW, f"{code}.json")
        if os.path.exists(path) and not force:
            continue
        url = SOURCE_URL.format(season=SEASON, code=code)
        log("downloading", url)
        with urllib.request.urlopen(url, timeout=60) as r, open(path, "wb") as f:
            f.write(r.read())
    return {code: json.load(open(os.path.join(RAW, f"{code}.json"), encoding="utf-8")) for code in LEAGUES}


# ========================== STEP 2: TRANSFORM =============================
def new_graph():
    g = Graph()
    for p, ns in [("fb", FB), ("res", RES), ("dbr", DBR), ("wd", WD), ("schema", SCHEMA), ("owl", OWL),
                  ("void", VOID), ("dcat", DCAT), ("dcterms", DCTERMS), ("foaf", FOAF), ("xsd", XSD)]:
        g.bind(p, ns)
    return g


def transform(raw):
    g = new_graph()
    clubs = {}  # club uri -> (name, country slug of its league = blocking key for linking)
    for cslug, (cname, *_rest) in COUNTRIES.items():
        c = RES[f"country/{cslug}"]
        g.add((c, RDF.type, FB.Country)); g.add((c, FB.name, Literal(cname, lang="en")))
    for code, data in raw.items():
        lslug, lname, cslug, cname, _, _ = LEAGUES[code]
        country, league = RES[f"country/{cslug}"], RES[f"league/{lslug}"]
        season = RES[f"season/{lslug}-{SEASON}"]
        g.add((league, RDF.type, FB.League)); g.add((league, FB.name, Literal(lname, lang="en")))
        g.add((league, FB.country, country))
        g.add((season, RDF.type, FB.Season)); g.add((season, FB.ofLeague, league))
        g.add((season, FB.name, Literal(data["name"], lang="en")))
        g.add((season, FB.seasonLabel, Literal(SEASON.replace("-", "/"))))
        g.add((season, DCTERMS.source, URIRef(SOURCE_URL.format(season=SEASON, code=code))))

        table = defaultdict(lambda: dict(p=0, w=0, d=0, l=0, gf=0, ga=0))
        for m in data["matches"]:
            if "score" not in m or "ft" not in m["score"]:
                continue  # unplayed / postponed fixture
            home, away = (RES["club/" + slug(m[k])] for k in ("team1", "team2"))
            for uri, name in ((home, m["team1"]), (away, m["team2"])):
                if uri not in clubs:
                    clubs[uri] = (name, cslug)
                    g.add((uri, RDF.type, FB.Club)); g.add((uri, FB.name, Literal(name)))
                    g.add((uri, FB.country, RES["country/" + CLUB_COUNTRY.get(name, cslug)]))
                g.add((season, FB.participant, uri))
            hs, as_ = m["score"]["ft"]
            mu = RES[f"match/{lslug}-{SEASON}/{m['date']}-{slug(m['team1'])}-vs-{slug(m['team2'])}"]
            g.add((mu, RDF.type, FB.Match)); g.add((mu, FB.inSeason, season))
            g.add((mu, RDFS.label, Literal(f"{m['team1']} {hs}-{as_} {m['team2']} ({m['date']})")))
            g.add((mu, FB.homeTeam, home)); g.add((mu, FB.awayTeam, away))
            g.add((mu, FB.kickOff, Literal(f"{m['date']}T{m.get('time') or '00:00'}:00", datatype=XSD.dateTime)))
            rnd = re.search(r"\d+", m.get("round", ""))
            if rnd:
                g.add((mu, FB.round, Literal(int(rnd.group()), datatype=XSD.integer)))
            g.add((mu, FB.homeScore, Literal(hs, datatype=XSD.nonNegativeInteger)))
            g.add((mu, FB.awayScore, Literal(as_, datatype=XSD.nonNegativeInteger)))
            if "ht" in m["score"]:
                g.add((mu, FB.homeHalfTimeScore, Literal(m["score"]["ht"][0], datatype=XSD.nonNegativeInteger)))
                g.add((mu, FB.awayHalfTimeScore, Literal(m["score"]["ht"][1], datatype=XSD.nonNegativeInteger)))
            res = FB.HomeWin if hs > as_ else FB.AwayWin if hs < as_ else FB.Draw
            g.add((mu, FB.result, res))
            if res != FB.Draw:
                g.add((mu, FB.winner, home if res == FB.HomeWin else away))
            for team, gf, ga in ((home, hs, as_), (away, as_, hs)):
                t = table[team]; t["p"] += 1; t["gf"] += gf; t["ga"] += ga
                t["w" if gf > ga else "l" if gf < ga else "d"] += 1

        # Final standings (points 3/1/0, ties broken by goal difference then goals scored)
        rows = sorted(table.items(), key=lambda kv: (-(3 * kv[1]["w"] + kv[1]["d"]),
                                                      -(kv[1]["gf"] - kv[1]["ga"]), -kv[1]["gf"]))
        for rank, (team, t) in enumerate(rows, 1):
            su = RES[f"standing/{lslug}-{SEASON}/{team.split('/')[-1]}"]
            nn = XSD.nonNegativeInteger
            g.add((su, RDF.type, FB.Standing)); g.add((su, FB.inSeason, season)); g.add((su, FB.team, team))
            g.add((su, RDFS.label, Literal(f"{clubs[team][0]} - {lname} {SEASON} (#{rank})")))
            g.add((su, FB.rank, Literal(rank, datatype=XSD.positiveInteger)))
            for prop, val in ((FB.played, t["p"]), (FB.won, t["w"]), (FB.drawn, t["d"]), (FB.lost, t["l"]),
                              (FB.goalsFor, t["gf"]), (FB.goalsAgainst, t["ga"]),
                              (FB.points, 3 * t["w"] + t["d"])):
                g.add((su, prop, Literal(val, datatype=nn)))
            g.add((su, FB.goalDifference, Literal(t["gf"] - t["ga"], datatype=XSD.integer)))
            if rank == 1:
                g.add((season, FB.champion, team))
    return g, clubs


# ============================ STEP 3: LINK ================================
STOP = {"fc", "f", "c", "cf", "afc", "ac", "sc", "sv", "ss", "ssc", "us", "ud", "ca", "rc", "rcd", "acf", "bc",
        "cfc", "osc", "hsc", "ogc", "tsg", "fsv", "calcio", "club", "football", "futbol", "de", "del", "la", "and"}


def norm_tokens(name):
    t = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower().replace(".", "").replace("&", " and ")
    return [w for w in re.split(r"[^a-z]+", t) if w and w not in STOP]


def similarity(a, b):
    """Silk-like aggregation: average(levenshtein-ratio, token jaccard) on normalised labels."""
    ta, tb = norm_tokens(a), norm_tokens(b)
    if not ta or not tb:
        return 0.0
    lev = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    jac = len(set(ta) & set(tb)) / len(set(ta) | set(tb))
    return round((lev + jac) / 2, 3)


def http_json(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json", "User-Agent": "lod-football/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def dbpedia_sparql(query):
    url = "https://dbpedia.org/sparql?" + urllib.parse.urlencode({"query": query, "format": "application/sparql-results+json"})
    return http_json(url)["results"]["bindings"]


def online_candidates(name):
    """DBpedia Lookup: candidate dbo:SoccerClub resources for a club name."""
    q = urllib.parse.urlencode({"query": name, "typeName": "SoccerClub", "maxResults": 10, "format": "json"})
    docs = http_json("https://lookup.dbpedia.org/api/search?" + q).get("docs", [])
    out = []
    for d in docs:
        uri = d["resource"][0]
        labels = [re.sub(r"</?B>", "", l) for l in d.get("label", [])]
        out.append((uri, labels or [uri.rsplit("/", 1)[-1].replace("_", " ")], ""))
    return out


def online_resolve(uris):
    """For each DBpedia URI: follow wikiPageRedirects to the resource that carries the data and read its
    owl:sameAs Wikidata QID. Returns {uri: (resource_with_data, qid)}."""
    values = " ".join(f"<{u}>" for u in uris)
    rows = dbpedia_sparql(f"""SELECT ?u ?k ?w WHERE {{ VALUES ?u {{ {values} }}
        {{ BIND(?u AS ?k) }} UNION {{ ?u <http://dbpedia.org/ontology/wikiPageRedirects> ?k }}
        ?k <{OWL.sameAs}> ?w FILTER(STRSTARTS(STR(?w), "{WD}")) }}""")
    out = {}
    for r in rows:
        out.setdefault(r["u"]["value"], (r["k"]["value"], r["w"]["value"][len(str(WD)):]))
    return out


def link(clubs, online=False, threshold=0.75):
    g = new_graph()
    # Countries and leagues: fixed, verified mappings (owl:sameAs DBpedia + Wikidata [+ GeoNames])
    for cslug, (_, dbp, qid, geo) in COUNTRIES.items():
        c = RES[f"country/{cslug}"]
        g.add((c, OWL.sameAs, DBR[dbp])); g.add((c, OWL.sameAs, WD[qid]))
        g.add((c, OWL.sameAs, URIRef(f"https://sws.geonames.org/{geo}/")))
    for code, (lslug, _, _, _, dbp, qid) in LEAGUES.items():
        l = RES[f"league/{lslug}"]
        g.add((l, OWL.sameAs, DBR[dbp])); g.add((l, OWL.sameAs, WD[qid]))
        g.add((l, RDFS.seeAlso, URIRef(f"https://en.wikipedia.org/wiki/{dbp}")))

    # Clubs: Silk-style link discovery
    report, linked = [], {}
    for uri, (name, cslug) in sorted(clubs.items()):
        cands = None
        if online:
            try:
                cands = online_candidates(name)
            except Exception as e:  # network problems -> fall back to snapshot
                log(f"  lookup failed for {name}: {e}; using snapshot")
        if not cands:  # blocking: only compare with clubs from the league's country (Silk RestrictTo)
            cands = [(str(DBR[t]), labels.split("|"), qid) for t, labels, qid in DBPEDIA_SNAPSHOT[cslug]]
        scored = sorted(((max(similarity(name, l) for l in labels), cu, qid) for cu, labels, qid in cands), reverse=True)
        best, target, qid = scored[0] if scored else (0, None, "")
        second = scored[1][0] if len(scored) > 1 else 0
        report.append((best, second, name, target))
        if best >= threshold:
            linked[uri] = [target, qid]

    if online and linked:  # resolve redirects + Wikidata QIDs on the live endpoint
        try:
            res = online_resolve({t for t, _ in linked.values()})
            for v in linked.values():
                if v[0] in res:
                    v[0], v[1] = res[v[0]]
        except Exception as e:
            log("  redirect/Wikidata resolution skipped:", e)

    for uri, (target, qid) in linked.items():
        g.add((uri, OWL.sameAs, URIRef(target)))
        if qid:
            g.add((uri, OWL.sameAs, WD[qid]))
        g.add((uri, RDFS.seeAlso, URIRef("https://en.wikipedia.org/wiki/" + target.rsplit("/", 1)[-1])))

    for best, second, name, target in report:
        mark = "OK " if best >= threshold else "-- "
        log(f"  {mark}{best:.3f} (next {second:.3f})  {name:30s} -> {target}")
    margin = min(b - s for b, s, *_ in report)
    log(f"linked {len(linked)}/{len(clubs)} clubs (threshold {threshold}, smallest margin to runner-up {margin:.3f})")
    return g


def verify_links(link_g):
    """Online check of every external link: DBpedia resource exists, is not a redirect stub and agrees
    with the Wikidata QID we publish; Wikidata QID's English Wikipedia article is reported."""
    pairs = defaultdict(set)
    for s, _, o in link_g.triples((None, OWL.sameAs, None)):
        pairs[s].add(str(o))
    dbp = sorted({o for v in pairs.values() for o in v if o.startswith(str(DBR))})
    values = " ".join(f"<{u}>" for u in dbp)
    rows = dbpedia_sparql(f"""SELECT ?u ?w ?r WHERE {{ VALUES ?u {{ {values} }}
        OPTIONAL {{ ?u <{OWL.sameAs}> ?w FILTER(STRSTARTS(STR(?w), "{WD}")) }}
        OPTIONAL {{ ?u <http://dbpedia.org/ontology/wikiPageRedirects> ?r }} }}""")
    info = defaultdict(set)
    for r in rows:
        if "w" in r: info[r["u"]["value"]].add(r["w"]["value"])
    bad = 0
    for s, targets in sorted(pairs.items()):
        d = [t for t in targets if t.startswith(str(DBR))]
        w = {t for t in targets if t.startswith(str(WD))}
        for t in d:
            ok = bool(info.get(t)) and (not w or w & info[t])
            if not ok:
                bad += 1
                log(f"  LINK PROBLEM {s} -> {t} (DBpedia Wikidata: {sorted(info.get(t, []))}, ours: {sorted(w)})")
    log(f"verified {len(dbp)} DBpedia targets against the live endpoint: {bad} problem(s)")
    return bad == 0


# =========================== STEP 4: DESCRIBE =============================
def describe(data_g, link_g):
    g = new_graph()
    ds = URIRef(BASE + "void#FootballLOD")
    g.add((ds, RDF.type, VOID.Dataset)); g.add((ds, RDF.type, DCAT.Dataset))
    g.add((ds, DCTERMS.title, Literal("European Football Linked Open Data 2023/24", lang="en")))
    g.add((ds, DCTERMS.description, Literal("Matches, clubs and final standings of the top-5 European leagues "
                                            "(2023/24) as 5-star Linked Open Data.", lang="en")))
    g.add((ds, DCTERMS.license, URIRef("https://creativecommons.org/publicdomain/zero/1.0/")))
    g.add((ds, DCTERMS.source, URIRef("https://github.com/openfootball/football.json")))
    g.add((ds, FOAF.homepage, URIRef(BASE)))
    g.add((ds, VOID.sparqlEndpoint, URIRef(BASE + "sparql")))
    g.add((ds, VOID.dataDump, URIRef(BASE + "dump.ttl")))
    g.add((ds, VOID.uriSpace, Literal(str(RES))))
    g.add((ds, DCTERMS.modified, Literal(__import__("datetime").date.today().isoformat(), datatype=XSD.date)))
    for fmt in ("Turtle", "RDF_XML", "JSON-LD", "N-Triples"):  # serialisations offered by the server
        g.add((ds, VOID.feature, URIRef("http://www.w3.org/ns/formats/" + fmt)))
    g.add((ds, VOID.exampleResource, RES["club/arsenal-fc"]))
    g.add((ds, VOID.vocabulary, URIRef(BASE + "ontology")))
    g.add((ds, VOID.vocabulary, URIRef(str(SCHEMA))))
    full = data_g + link_g
    g.add((ds, VOID.triples, Literal(len(full), datatype=XSD.integer)))
    g.add((ds, VOID.entities, Literal(len(set(full.subjects(RDF.type, None))), datatype=XSD.integer)))
    for cls in (FB.Club, FB.Match, FB.Standing, FB.Season, FB.League, FB.Country):
        part = BNode()
        g.add((ds, VOID.classPartition, part)); g.add((part, VOID["class"], cls))
        g.add((part, VOID.entities, Literal(len(set(full.subjects(RDF.type, cls))), datatype=XSD.integer)))
    for target, prefix in (("DBpedia", "http://dbpedia.org/"), ("Wikidata", "http://www.wikidata.org/"),
                           ("GeoNames", "https://sws.geonames.org/")):
        n = sum(1 for _, _, o in link_g.triples((None, OWL.sameAs, None)) if str(o).startswith(prefix))
        if not n:
            continue
        ls, tgt = URIRef(BASE + f"void#{target}Links"), URIRef(BASE + f"void#{target}")
        g.add((tgt, RDF.type, VOID.Dataset)); g.add((tgt, FOAF.homepage, URIRef(prefix)))
        g.add((ls, RDF.type, VOID.Linkset)); g.add((ls, VOID.linkPredicate, OWL.sameAs))
        g.add((ls, VOID.subjectsTarget, ds)); g.add((ls, VOID.objectsTarget, tgt))
        g.add((ls, VOID.triples, Literal(n, datatype=XSD.integer))); g.add((ds, VOID.subset, ls))
    return g


# ============================== QUALITY CHECKS ==============================
def check(online=False):
    """1) SHACL data-quality validation, 2) OWL 2 DL consistency with HermiT (if owlready2 + Java
    are installed), 3) with --online: every published DBpedia link is re-verified on the live endpoint."""
    import tempfile
    g = Graph()
    with open(os.path.join(HERE, "ontology.ttl"), encoding="utf-8") as f:
        g.parse(data=f.read().replace("http://localhost:5000/", BASE), format="turtle")
    for fn in ("football.ttl", "links.ttl", "void.ttl"):
        g.parse(os.path.join(DATA, fn), format="turtle")
    ok = True
    try:
        import pyshacl
        with open(os.path.join(HERE, "shapes.ttl"), encoding="utf-8") as f:
            shapes = Graph().parse(data=f.read().replace("http://localhost:5000/", BASE), format="turtle")
        conforms, _, text = pyshacl.validate(g, shacl_graph=shapes, advanced=True)
        log("SHACL validation:", "CONFORMS" if conforms else "VIOLATIONS")
        if not conforms:
            print(text); ok = False
    except ImportError:
        log("SHACL validation skipped (pip install pyshacl)")
    try:
        import owlready2
        with tempfile.TemporaryDirectory() as tmp:
            nt = os.path.join(tmp, "all.nt"); g.serialize(nt, format="nt", encoding="utf-8")
            world = owlready2.World()
            with open(nt, "rb") as f:
                world.get_ontology("http://localhost/check-graph").load(fileobj=f, format="ntriples")
            try:
                owlready2.sync_reasoner_hermit(world, infer_property_values=False, debug=0)
                unsat = list(world.inconsistent_classes())
                log("OWL 2 reasoning (HermiT): CONSISTENT, unsatisfiable classes:", unsat or "none")
                ok &= not unsat
            except owlready2.OwlReadyInconsistentOntologyError:
                log("OWL 2 reasoning (HermiT): INCONSISTENT"); ok = False
    except ImportError:
        log("OWL reasoning skipped (pip install owlready2, needs Java)")
    except Exception as e:
        log("OWL reasoning skipped:", e)
    if online:
        ok &= verify_links(Graph().parse(os.path.join(DATA, "links.ttl")))
    log("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--download", action="store_true", help="re-download raw JSON")
    ap.add_argument("--online", action="store_true", help="link live against DBpedia Lookup/SPARQL")
    ap.add_argument("--threshold", type=float, default=0.75, help="link acceptance threshold (0-1)")
    ap.add_argument("--check", action="store_true", help="only run quality checks (SHACL, OWL reasoner; "
                                                          "with --online also verify links on DBpedia)")
    a = ap.parse_args()
    if a.check:
        sys.exit(0 if check(a.online) else 1)
    os.makedirs(DATA, exist_ok=True)
    log("1/4 collect");   raw = collect(a.download)
    log("2/4 transform"); data_g, clubs = transform(raw)
    log("3/4 link");      link_g = link(clubs, a.online, a.threshold)
    log("4/4 describe");  void_g = describe(data_g, link_g)
    for g, fn in ((data_g, "football.ttl"), (link_g, "links.ttl"), (void_g, "void.ttl")):
        g.serialize(os.path.join(DATA, fn), format="turtle", encoding="utf-8")
        log(f"wrote data/{fn} ({len(g)} triples)")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
