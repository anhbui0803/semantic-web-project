"""
server.py - SPARQL endpoint, Linked Data front-end and terminal client (Topic 1, step 5)

  python server.py                       # web UI + SPARQL endpoint on http://localhost:5000
  python server.py query "SELECT ..."    # run one query in the terminal
  python server.py query -f my.rq        # run a query file
  python server.py shell                 # interactive SPARQL terminal (end a query with ';')

HTTP interface (W3C SPARQL 1.1 Protocol + Cool URIs for the Semantic Web):
  GET/POST /sparql?query=...     results as JSON / XML / CSV / TSV, CONSTRUCT/DESCRIBE as Turtle / RDF/XML / JSON-LD
  GET /resource/{type}/{id}      303 See Other -> /page/... (HTML) or /data/....ttl (RDF) by Accept header
  GET /data/{type}/{id}.{ttl|rdf|jsonld|nt}
  GET /ontology  /void  /dump.ttl
"""
import os, sys, html, urllib.parse
from rdflib import Graph, URIRef, Literal
from rdflib.namespace import RDF, RDFS, OWL, FOAF, DCTERMS, Namespace

VOID = Namespace("http://rdfs.org/ns/void#")

BASE = os.environ.get("LOD_BASE", "http://localhost:5000/")
HERE = os.path.dirname(os.path.abspath(__file__))
FORMATS = {"ttl": ("turtle", "text/turtle"), "rdf": ("xml", "application/rdf+xml"),
           "jsonld": ("json-ld", "application/ld+json"), "nt": ("nt", "application/n-triples")}

EXAMPLES = {
    "Champions of every league": """PREFIX fb: <{B}ontology#>
SELECT ?league ?season ?club ?points WHERE {
  ?s a fb:Season ; fb:ofLeague/fb:name ?league ; fb:seasonLabel ?season ; fb:champion ?c .
  ?c fb:name ?club .
  ?st fb:inSeason ?s ; fb:team ?c ; fb:points ?points .
} ORDER BY DESC(?points)""",
    "Premier League table": """PREFIX fb: <{B}ontology#>
SELECT ?rank ?club ?played ?won ?drawn ?lost ?gd ?points WHERE {
  ?st fb:inSeason ?s ; fb:rank ?rank ; fb:team/fb:name ?club ; fb:played ?played ;
      fb:won ?won ; fb:drawn ?drawn ; fb:lost ?lost ; fb:goalDifference ?gd ; fb:points ?points .
  ?s fb:ofLeague/fb:name "Premier League"@en .
} ORDER BY ?rank""",
    "Biggest wins (goal margin >= 5)": """PREFIX fb: <{B}ontology#>
SELECT ?date ?home ?hs ?as ?away WHERE {
  ?m a fb:Match ; fb:kickOff ?k ; fb:homeTeam/fb:name ?home ; fb:awayTeam/fb:name ?away ;
     fb:homeScore ?hs ; fb:awayScore ?as .
  FILTER(ABS(?hs - ?as) >= 5) BIND(SUBSTR(STR(?k), 1, 10) AS ?date)
} ORDER BY DESC(ABS(?hs - ?as)) ?date""",
    "Home / draw / away ratio per league": """PREFIX fb: <{B}ontology#>
SELECT ?league (COUNT(?m) AS ?matches)
       (SUM(IF(?r = fb:HomeWin, 1, 0)) AS ?homeWins)
       (SUM(IF(?r = fb:Draw, 1, 0)) AS ?draws)
       (SUM(IF(?r = fb:AwayWin, 1, 0)) AS ?awayWins)
       (ROUND(100 * AVG(?hs + ?as)) / 100 AS ?goalsPerMatch) WHERE {
  ?m fb:inSeason/fb:ofLeague/fb:name ?league ; fb:result ?r ; fb:homeScore ?hs ; fb:awayScore ?as .
} GROUP BY ?league ORDER BY DESC(?goalsPerMatch)""",
    "Comebacks (losing at half-time, won at full-time)": """PREFIX fb: <{B}ontology#>
SELECT ?date ?home ?halfTime ?fullTime ?away ?winner WHERE {
  ?m fb:kickOff ?k ; fb:winner ?w ; fb:homeTeam ?h ; fb:awayTeam ?a ;
     fb:homeHalfTimeScore ?hh ; fb:awayHalfTimeScore ?ah ; fb:homeScore ?hs ; fb:awayScore ?as .
  FILTER((?w = ?h && ?hh < ?ah) || (?w = ?a && ?ah < ?hh))
  ?h fb:name ?home . ?a fb:name ?away . ?w fb:name ?winner .
  BIND(SUBSTR(STR(?k), 1, 10) AS ?date)
  BIND(CONCAT(STR(?hh), "-", STR(?ah)) AS ?halfTime) BIND(CONCAT(STR(?hs), "-", STR(?as)) AS ?fullTime)
} ORDER BY ?date""",
    "5-star links: clubs -> DBpedia / Wikidata": """PREFIX fb: <{B}ontology#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
SELECT ?club ?country ?external WHERE {
  ?c a fb:Club ; fb:name ?club ; fb:country ?k ; owl:sameAs ?external .
  ?k owl:sameAs ?ext . FILTER(STRSTARTS(STR(?ext), "http://dbpedia.org/"))
  BIND(STRAFTER(STR(?ext), "resource/") AS ?country)
} ORDER BY ?country ?club""",
    "Federated: champions' stadium from DBpedia via owl:sameAs (needs Internet)": """PREFIX fb:  <{B}ontology#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX dbo: <http://dbpedia.org/ontology/>
SELECT ?league ?club ?stadium WHERE {
  ?s fb:champion ?c ; fb:ofLeague/fb:name ?league . ?c fb:name ?club ; owl:sameAs ?dbp .
  FILTER(STRSTARTS(STR(?dbp), "http://dbpedia.org/resource/"))
  SERVICE <https://dbpedia.org/sparql> {
    { ?dbp dbo:ground ?g } UNION { ?alias dbo:wikiPageRedirects ?dbp ; dbo:ground ?g }
  }
  BIND(REPLACE(STRAFTER(STR(?g), "resource/"), "_", " ") AS ?stadium)
} ORDER BY ?league""",
    "Ontology: classes and their super-classes": """PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?class ?label ?super WHERE {
  ?class a owl:Class ; rdfs:label ?label . FILTER(lang(?label) = "en" && isIRI(?class))
  OPTIONAL { ?class rdfs:subClassOf ?super FILTER(isIRI(?super)) }
}""",
}
EXAMPLES = {k: v.replace("{B}", BASE) for k, v in EXAMPLES.items()}


def load_graph():
    g = Graph()
    with open(os.path.join(HERE, "ontology.ttl"), encoding="utf-8") as f:
        g.parse(data=f.read().replace("http://localhost:5000/", BASE), format="turtle")
    for fn in ("football.ttl", "links.ttl", "void.ttl"):
        path = os.path.join(HERE, "data", fn)
        if not os.path.exists(path):
            sys.exit(f"Missing {path} - run `python build.py` first.")
        g.parse(path, format="turtle")
    g.bind("fb", BASE + "ontology#"); g.bind("res", BASE + "resource/")
    return g


# ------------------------------- terminal --------------------------------
def print_result(res):
    if res.type in ("CONSTRUCT", "DESCRIBE"):
        print(res.graph.serialize(format="turtle")); return
    if res.type == "ASK":
        print(res.askAnswer); return
    cols = [str(v) for v in res.vars]
    rows = [["" if r[c] is None else shorten(r[c]) for c in cols] for r in res]
    w = [max([len(c)] + [len(r[i]) for r in rows]) for i, c in enumerate(cols)]
    line = "+" + "+".join("-" * (x + 2) for x in w) + "+"
    print(line); print("| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(cols)) + " |"); print(line)
    for r in rows:
        print("| " + " | ".join(v.ljust(w[i]) for i, v in enumerate(r)) + " |")
    print(line); print(f"{len(rows)} row(s)")


def shorten(term):
    s = str(term)
    for p, ns in (("res:", BASE + "resource/"), ("fb:", BASE + "ontology#"), ("dbr:", "http://dbpedia.org/resource/"),
                  ("wd:", "http://www.wikidata.org/entity/"), ("rdf:", str(RDF)), ("rdfs:", str(RDFS)),
                  ("owl:", str(OWL)), ("xsd:", "http://www.w3.org/2001/XMLSchema#")):
        if isinstance(term, URIRef) and s.startswith(ns):
            return p + s[len(ns):]
    return s


def shell(g):
    print("SPARQL terminal - %d triples loaded. End a query with ';'  |  :examples  :quit" % len(g))
    names = list(EXAMPLES)
    buf = []
    while True:
        try:
            line = input("sparql> " if not buf else "   ...> ")
        except (EOFError, KeyboardInterrupt):
            print(); break
        cmd = line.strip()
        if not buf and cmd in (":q", ":quit", "exit"):
            break
        if not buf and cmd == ":examples":
            for i, n in enumerate(names, 1):
                print(f"  :{i}  {n}")
            continue
        if not buf and cmd[1:].isdigit() and cmd.startswith(":"):
            i = int(cmd[1:]) - 1
            if 0 <= i < len(names):
                q = EXAMPLES[names[i]]; print(q)
                run_safely(g, q)
            continue
        buf.append(line)
        if cmd.endswith(";"):
            run_safely(g, "\n".join(buf).rstrip().rstrip(";")); buf = []


def run_safely(g, q):
    try:
        print_result(g.query(q))
    except Exception as e:
        print("Error:", e)


# --------------------------------- web -----------------------------------
def create_app(g):
    from flask import Flask, request, Response, redirect, abort

    app = Flask(__name__)

    def wants_html():
        best = request.accept_mimetypes.best_match(["text/html", "text/turtle", "application/rdf+xml",
                                                     "application/ld+json", "application/n-triples"])
        return best in (None, "text/html")

    def rdf_ext():
        best = request.accept_mimetypes.best_match([m for _, m in FORMATS.values()]) or "text/turtle"
        return next(k for k, (_, m) in FORMATS.items() if m == best)

    @app.after_request
    def cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @app.route("/sparql", methods=["GET", "POST"])
    def sparql():
        if request.values.get("update") is not None or request.mimetype == "application/sparql-update":
            return Response("This endpoint is read-only (SPARQL Update is not allowed)", status=403)
        q = request.values.get("query")
        if q is None and request.mimetype == "application/sparql-query":
            q = request.get_data(as_text=True)
        if not q:
            return redirect("/")
        try:
            res = g.query(q)
        except Exception as e:
            return Response(f"Query error: {e}", status=400, mimetype="text/plain")
        fmt = request.values.get("format", "")
        accept = request.accept_mimetypes
        if res.type in ("CONSTRUCT", "DESCRIBE"):
            ext = fmt if fmt in FORMATS else rdf_ext()
            rf, mt = FORMATS[ext]
            return Response(res.graph.serialize(format=rf), mimetype=mt)
        table = {"json": ("json", "application/sparql-results+json"), "xml": ("xml", "application/sparql-results+xml"),
                 "csv": ("csv", "text/csv"), "tsv": ("txt", "text/tab-separated-values")}
        if fmt not in table:
            best = accept.best_match([m for _, m in table.values()])
            fmt = next((k for k, (_, m) in table.items() if m == best), "json")
        rf, mt = table[fmt]
        out = res.serialize(format=rf)
        return Response(out, mimetype=mt)

    @app.route("/resource/<path:p>")
    def resource(p):  # Cool URIs: 303 redirect to a document about the thing
        if (URIRef(BASE + "resource/" + p), None, None) not in g:
            abort(404)
        resp = redirect(f"/page/{p}" if wants_html() else f"/data/{p}.{rdf_ext()}", code=303)
        resp.headers["Vary"] = "Accept"
        return resp

    @app.route("/data/<path:p>")
    def data(p):
        stem, _, ext = p.rpartition(".")
        if ext not in FORMATS:
            abort(404)
        uri = URIRef(BASE + "resource/" + stem)
        d = describe(uri)
        if not len(d):
            abort(404)
        # Describe the RDF document itself (Linked Data best practice): topic, licence, dataset
        doc = URIRef(BASE + "data/" + p)
        d.add((doc, RDF.type, FOAF.Document)); d.add((doc, FOAF.primaryTopic, uri))
        d.add((doc, DCTERMS.license, URIRef("https://creativecommons.org/publicdomain/zero/1.0/")))
        d.add((doc, VOID.inDataset, URIRef(BASE + "void#FootballLOD")))
        rf, mt = FORMATS[ext]
        return Response(d.serialize(format=rf), mimetype=mt)

    def describe(uri):
        d = Graph(namespace_manager=g.namespace_manager)
        for t in g.triples((uri, None, None)): d.add(t)
        for t in g.triples((None, None, uri)): d.add(t)
        return d

    @app.route("/page/<path:p>")
    def page(p):
        uri = URIRef(BASE + "resource/" + p)
        if (uri, None, None) not in g:
            abort(404)
        label = g.value(uri, RDFS.label) or g.value(uri, URIRef(BASE + "ontology#name")) or p
        out_rows = "".join(f"<tr><td>{link(pr)}</td><td>{link(o)}</td></tr>"
                           for pr, o in sorted(g.predicate_objects(uri)))
        in_rows = "".join(f"<tr><td>{link(s)}</td><td>{link(pr)}</td></tr>"
                          for s, pr in sorted(g.subject_predicates(uri)))[:200000]
        alt = " · ".join(f'<a href="/data/{p}.{e}">{e}</a>' for e in FORMATS)
        body = (f"<h1>{html.escape(str(label))}</h1><p><code>{html.escape(str(uri))}</code><br>RDF: {alt}</p>"
                f"<h2>Properties</h2><table>{out_rows}</table>")
        if in_rows:
            body += f"<h2>Referenced by</h2><table>{in_rows}</table>"
        return layout(str(label), body)

    def link(term):
        if isinstance(term, Literal):
            dt = f' <small>{shorten(URIRef(term.datatype))}</small>' if term.datatype else (
                f" <small>@{term.language}</small>" if term.language else "")
            return html.escape(str(term)) + dt
        s = str(term)
        href = s.replace(BASE + "resource/", "/page/") if s.startswith(BASE + "resource/") else (
            "/ontology" if s.startswith(BASE + "ontology") else s)
        name = g.value(term, URIRef(BASE + "ontology#name")) or g.value(term, RDFS.label)
        text = html.escape(shorten(term)) + (f" <small>({html.escape(str(name))})</small>" if name and
                                             s.startswith(BASE + "resource/") else "")
        return f'<a href="{html.escape(href)}">{text}</a>'

    @app.route("/ontology")
    def ontology():
        with open(os.path.join(HERE, "ontology.ttl"), encoding="utf-8") as f:
            ttl = f.read().replace("http://localhost:5000/", BASE)
        if wants_html():
            return layout("Ontology", f"<h1>Ontology</h1><pre>{html.escape(ttl)}</pre>")
        return Response(ttl, mimetype="text/turtle")

    @app.route("/void")
    @app.route("/.well-known/void")
    def void():
        with open(os.path.join(HERE, "data", "void.ttl"), encoding="utf-8") as f:
            ttl = f.read()
        if wants_html():
            return layout("VoID", f"<h1>Dataset description (VoID)</h1><pre>{html.escape(ttl)}</pre>")
        return Response(ttl, mimetype="text/turtle")

    @app.route("/dump.ttl")
    def dump():
        return Response(g.serialize(format="turtle"), mimetype="text/turtle",
                        headers={"Content-Disposition": "attachment; filename=football-lod.ttl"})

    @app.route("/")
    def index():
        opts = "".join(f'<option value="{html.escape(v)}">{html.escape(k)}</option>' for k, v in EXAMPLES.items())
        first = html.escape(next(iter(EXAMPLES.values())))
        body = f"""<h1>European Football Linked Open Data</h1>
<p>{len(g):,} triples · <a href="/ontology">ontology</a> · <a href="/void">VoID</a> ·
<a href="/dump.ttl">dump</a> · <a href="/page/league/premier-league">browse</a> ·
endpoint: <code>{BASE}sparql</code></p>
<select id="ex" onchange="q.value=this.value">{opts}</select>
<textarea id="q" spellcheck="false">{first}</textarea>
<p><button onclick="run()">Run query</button> <select id="fmt"><option>table</option><option>json</option>
<option>xml</option><option>csv</option></select> <span id="st"></span></p><div id="out"></div>
<script>
const B = {repr(BASE)};
function cell(v) {{
  if (!v) return '';
  const t = v.value.replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c]));
  if (v.type !== 'uri') return t;
  const href = v.value.startsWith(B + 'resource/') ? v.value.replace(B + 'resource/', '/page/') : v.value;
  return `<a href="${{href}}">${{t.replace(B + 'resource/', 'res:')}}</a>`;
}}
async function run() {{
  const f = document.getElementById('fmt').value, st = document.getElementById('st'),
        out = document.getElementById('out'), t0 = performance.now();
  st.textContent = 'running...';
  const r = await fetch('/sparql', {{method: 'POST', body: new URLSearchParams({{query: q.value,
            format: f === 'table' ? 'json' : f}})}});
  const txt = await r.text();
  st.textContent = r.status + ' · ' + Math.round(performance.now() - t0) + ' ms';
  if (!r.ok || f !== 'table' || !r.headers.get('content-type').includes('sparql-results+json')) {{
    out.innerHTML = '<pre></pre>'; out.firstChild.textContent = txt; return; }}
  const j = JSON.parse(txt);
  if ('boolean' in j) {{ out.textContent = j.boolean; return; }}
  const v = j.head.vars;
  out.innerHTML = `<p>${{j.results.bindings.length}} rows</p><table><tr>${{v.map(x => `<th>${{x}}</th>`).join('')}}</tr>` +
    j.results.bindings.map(b => `<tr>${{v.map(x => `<td>${{cell(b[x])}}</td>`).join('')}}</tr>`).join('') + '</table>';
}}
</script>"""
        return layout("SPARQL", body)

    def layout(title, body):
        return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)} - Football LOD</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:auto;padding:16px;color:#1d2433}}
a{{color:#0b5cad;text-decoration:none}}a:hover{{text-decoration:underline}}
textarea{{width:100%;height:230px;font:13px monospace;box-sizing:border-box}}select,button{{font:inherit;padding:4px 8px}}
table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border:1px solid #d6dbe4;padding:4px 8px;text-align:left;vertical-align:top}}
th{{background:#eef2f8}}pre{{background:#f5f7fa;padding:12px;overflow:auto;font-size:13px}}code{{font-size:13px}}
header{{border-bottom:1px solid #d6dbe4;margin-bottom:12px;padding-bottom:6px}}</style></head>
<body><header><a href="/"><b>⚽ Football LOD</b></a> · SPARQL endpoint · Linked Data browser</header>{body}</body></html>"""

    return app


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    graph = load_graph()
    if args[:1] == ["query"]:
        if args[1:2] == ["-f"]:
            with open(args[2], encoding="utf-8") as f:
                run_safely(graph, f.read())
        else:
            run_safely(graph, " ".join(args[1:]))
    elif args[:1] == ["shell"]:
        shell(graph)
    else:
        port = int(urllib.parse.urlparse(BASE).port or 80)
        print(f"Loaded {len(graph)} triples. Open {BASE}  (SPARQL endpoint: {BASE}sparql)")
        create_app(graph).run(host="0.0.0.0", port=port)
