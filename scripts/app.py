#!/usr/bin/env python3
"""
app.py — Step 5 of Topic 1: publish the 5★ dataset on the web.

Routes
------
  /                         Web UI (query editor + example queries)
  /sparql                   SPARQL 1.1 Protocol endpoint (read-only)
                              GET  /sparql?query=...
                              POST /sparql  (application/x-www-form-urlencoded: query=...)
                              POST /sparql  (Content-Type: application/sparql-query)
                            Result format by Accept header or ?format=:
                              SELECT/ASK      → application/sparql-results+json (default),
                                                application/sparql-results+xml, text/csv
                              CONSTRUCT/DESCRIBE → text/turtle (default), application/n-triples,
                                                application/rdf+xml, application/ld+json
  /data/<type>/<id>         Dereferenceable entity URIs (4★ "use URIs so people can
                            look things up"): HTML for browsers, RDF for machines
                            (content negotiation, or a .ttl/.nt/.rdf/.jsonld suffix)
  /ontology                 The ontology (same content negotiation)
  /void, /.well-known/void  VoID dataset description
  /dump/<file>              Download the dataset files
  /api/query                JSON used by the web UI

Entity URIs are http://semantic-football.org/data/<type>/<id>. When the app
runs locally, http://127.0.0.1:5000/data/<type>/<id> serves the same
resource; deploying the app at the semantic-football.org domain (or behind a
w3id.org redirect) makes the URIs themselves resolvable.

Run locally:  python scripts/app.py        (env: PORT, HOST)
Production:   gunicorn --chdir scripts app:app   (see render.yaml)
              FOOTBALL_PUBLIC_BASE (or Render's RENDER_EXTERNAL_URL) sets the
              public address used in the VoID description.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path

from flask import (Flask, Response, abort, jsonify, render_template, request,
                   send_from_directory)
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS

from common import (BASE_URI, DATA, DATASET_URI, LINKED_DIR, ONTO_TTL, PREFIX_BLOCK,
                    RDF_DIR, UPDATE_FORMS, VOID_TTL, bind_prefixes, load_example_queries,
                    load_graph, query_type, shorten)

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))

# The VoID file is generated with http://127.0.0.1:5000 as the server address.
# When the app runs elsewhere (e.g. on Render), point those links to the real host.
BUILD_BASE = "http://127.0.0.1:5000"
PUBLIC_BASE = (os.environ.get("FOOTBALL_PUBLIC_BASE")
               or os.environ.get("RENDER_EXTERNAL_URL") or BUILD_BASE).rstrip("/")


def rebase(g: Graph) -> Graph:
    """Rewrite server URLs inside the VoID description to PUBLIC_BASE."""
    if PUBLIC_BASE == BUILD_BASE:
        return g
    fix = lambda n: URIRef(PUBLIC_BASE + str(n)[len(BUILD_BASE):]) \
        if isinstance(n, URIRef) and str(n).startswith(BUILD_BASE) else n
    for t in [t for t in g if any(isinstance(n, URIRef) and str(n).startswith(BUILD_BASE) for n in t)]:
        g.remove(t)
        g.add(tuple(fix(n) for n in t))
    return g


print("Loading RDF graph…")
G = rebase(load_graph("5star"))
ONTOLOGY = bind_prefixes(Graph()).parse(ONTO_TTL, format="turtle")
TRIPLE_COUNT = len(G)
EXAMPLES = load_example_queries()
print(f"Loaded {TRIPLE_COUNT} triples, {len(EXAMPLES)} example queries.")

RDF_FORMATS = {                       # mime type → rdflib format
    "text/turtle": "turtle",
    "application/n-triples": "nt",
    "application/rdf+xml": "xml",
    "application/ld+json": "json-ld",
}
SUFFIX_FORMATS = {".ttl": "text/turtle", ".nt": "application/n-triples",
                  ".rdf": "application/rdf+xml", ".jsonld": "application/ld+json"}
RESULT_FORMATS = {                    # mime type → rdflib result serializer
    "application/sparql-results+json": "json",
    "application/json": "json",
    "application/sparql-results+xml": "xml",
    "application/xml": "xml",
    "text/csv": "csv",
}
FORMAT_ALIASES = {"json": "application/sparql-results+json", "xml": "application/sparql-results+xml",
                  "csv": "text/csv", "turtle": "text/turtle", "ttl": "text/turtle",
                  "nt": "application/n-triples", "ntriples": "application/n-triples",
                  "rdfxml": "application/rdf+xml", "jsonld": "application/ld+json",
                  "json-ld": "application/ld+json"}


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────
def negotiate(offers: list[str], default: str) -> str:
    fmt = request.args.get("format", "").strip().lower()
    if fmt:
        return FORMAT_ALIASES.get(fmt, fmt)
    best = request.accept_mimetypes.best_match(offers)
    if not best or (request.accept_mimetypes.best == "*/*" and default in offers):
        return default
    return best


def rdf_response(g: Graph, mime: str, status: int = 200) -> Response:
    data = g.serialize(format=RDF_FORMATS[mime])
    return Response(data, status=status, mimetype=mime,
                    headers={"Vary": "Accept", "Content-Type": f"{mime}; charset=utf-8"})


def error(msg: str, status: int = 400) -> Response:
    return Response(msg + "\n", status=status, mimetype="text/plain")


def run_sparql(raw: str):
    """Execute a read-only query with the standard prefixes available."""
    if query_type(raw) in UPDATE_FORMS:
        raise PermissionError("SPARQL Update is not allowed on this read-only endpoint.")
    return G.query(PREFIX_BLOCK + "\n" + raw)


# ─────────────────────────────────────────────────────────────
#  Web UI
# ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    examples = {k: {"label": k.replace("_", " ").capitalize(), "query": v["query"],
                    "description": v["description"], "requires": sorted(v["requires"])}
                for k, v in EXAMPLES.items()}
    return render_template("index.html", examples=examples,
                           examples_json=json.dumps(examples),
                           triple_count=f"{TRIPLE_COUNT:,}")


def fmt_value(node):
    if node is None:
        return None
    if isinstance(node, URIRef):
        return {"type": "uri", "value": str(node)}
    if isinstance(node, Literal):
        return {"type": "literal", "value": str(node)}
    return {"type": "bnode", "value": str(node)}


@app.route("/api/query", methods=["POST"])
@app.route("/query", methods=["POST"])          # kept for backward compatibility
def api_query():
    raw = (request.get_json(force=True) or {}).get("query", "").strip()
    if not raw:
        return jsonify({"error": "Empty query"})
    try:
        result = run_sparql(raw)
    except Exception as e:
        return jsonify({"error": str(e)})
    if result.type == "ASK":
        return jsonify({"type": "ask", "result": bool(result.askAnswer)})
    if result.type in ("DESCRIBE", "CONSTRUCT"):
        triples = [[str(s), str(p), str(o), "uri" if isinstance(o, URIRef) else "literal"]
                   for s, p, o in sorted(result.graph, key=lambda t: (str(t[0]), str(t[1])))]
        return jsonify({"type": "describe", "triples": triples})
    vars_ = [str(v) for v in result.vars]
    rows = [{str(v): fmt_value(row[v]) for v in result.vars} for row in result]
    return jsonify({"type": "select", "vars": vars_, "rows": rows})


# ─────────────────────────────────────────────────────────────
#  SPARQL 1.1 Protocol endpoint
# ─────────────────────────────────────────────────────────────
SERVICE_DESCRIPTION = f"""@prefix sd: <http://www.w3.org/ns/sparql-service-description#> .
@prefix void: <http://rdfs.org/ns/void#> .
<> a sd:Service ;
   sd:endpoint <> ;
   sd:supportedLanguage sd:SPARQL11Query ;
   sd:resultFormat <http://www.w3.org/ns/formats/SPARQL_Results_JSON>,
                   <http://www.w3.org/ns/formats/SPARQL_Results_XML>,
                   <http://www.w3.org/ns/formats/SPARQL_Results_CSV>,
                   <http://www.w3.org/ns/formats/Turtle>,
                   <http://www.w3.org/ns/formats/N-Triples>,
                   <http://www.w3.org/ns/formats/RDF_XML>,
                   <http://www.w3.org/ns/formats/JSON-LD> ;
   sd:defaultDataset [ a sd:Dataset ; void:triples {TRIPLE_COUNT} ;
                       sd:defaultGraph [ a sd:Graph ; void:inDataset <{DATASET_URI}> ] ] .
"""


@app.route("/sparql", methods=["GET", "POST"])
def sparql_endpoint():
    if request.method == "POST":
        ctype = (request.content_type or "").split(";")[0].strip()
        if ctype == "application/sparql-query":
            raw = request.get_data(as_text=True)
        elif ctype == "application/sparql-update" or "update" in request.form:
            return error("SPARQL Update is not supported (read-only endpoint).", 403)
        else:
            raw = request.form.get("query", "")
    else:
        if "update" in request.args:
            return error("SPARQL Update is not supported (read-only endpoint).", 403)
        raw = request.args.get("query", "")

    if not raw.strip():
        # No query: humans get the UI, machines get a SPARQL service description.
        if request.accept_mimetypes.best_match(["text/html", "text/turtle"]) == "text/html":
            return index()
        return Response(SERVICE_DESCRIPTION, mimetype="text/turtle")

    try:
        result = run_sparql(raw)
    except PermissionError as e:
        return error(str(e), 403)
    except Exception as e:
        return error(f"Malformed query or evaluation error: {e}", 400)

    if result.type in ("CONSTRUCT", "DESCRIBE"):
        mime = negotiate(list(RDF_FORMATS), "text/turtle")
        if mime not in RDF_FORMATS:
            return error(f"Unsupported format for {result.type}: {mime}", 406)
        return rdf_response(bind_prefixes(result.graph), mime)

    mime = negotiate(list(RESULT_FORMATS), "application/sparql-results+json")
    if mime not in RESULT_FORMATS:
        return error(f"Unsupported format for {result.type}: {mime}", 406)
    body = result.serialize(format=RESULT_FORMATS[mime])
    if mime == "application/json":
        mime = "application/sparql-results+json"
    return Response(body, mimetype=mime, headers={"Vary": "Accept"})


# ─────────────────────────────────────────────────────────────
#  Dereferenceable URIs (Linked Data)
# ─────────────────────────────────────────────────────────────
def describe(uri: URIRef) -> Graph:
    """Outgoing + incoming triples, plus labels of the linked resources."""
    out = bind_prefixes(Graph())
    for t in G.triples((uri, None, None)):
        out.add(t)
        if isinstance(t[2], BNode):
            for t2 in G.triples((t[2], None, None)):
                out.add(t2)
    for t in G.triples((None, None, uri)):
        out.add(t)
    for node in set(out.subjects()) | set(out.objects()):
        if isinstance(node, URIRef) and node != uri:
            for lab in G.objects(node, RDFS.label):
                out.add((node, RDFS.label, lab))
    return out


def href(node) -> str:
    s = str(node)
    if s.startswith(BASE_URI):
        return s[len(BASE_URI) - 1:] or "/"
    return s


def label_of(node) -> str:
    lab = G.value(node, RDFS.label) if isinstance(node, URIRef) else None
    return str(lab) if lab else shorten(str(node))


RESOURCE_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Football Linked Data</title>
<link rel="alternate" type="text/turtle" href="{path}.ttl">
<link rel="alternate" type="application/ld+json" href="{path}.jsonld">
<style>
 body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0f1117;color:#e2e8f0;margin:0;padding:24px;}}
 a{{color:#76e4f7;text-decoration:none}} a:hover{{text-decoration:underline}}
 h1{{margin:0 0 4px;font-size:24px}} .uri{{color:#718096;font-size:13px;word-break:break-all}}
 .formats{{margin:12px 0 20px;font-size:13px}} .formats a{{margin-right:12px}}
 table{{border-collapse:collapse;width:100%;font-size:14px}}
 th{{text-align:left;color:#90cdf4;border-bottom:2px solid #2d3748;padding:8px}}
 td{{border-bottom:1px solid #1e2535;padding:8px;vertical-align:top;word-break:break-word}}
 td.p{{color:#d6bcfa;white-space:nowrap;width:1%}} .lit{{color:#9ae6b4}} .ext::after{{content:" ↗"}}
 h2{{font-size:16px;color:#a0aec0;margin-top:28px}} img{{max-height:160px;border-radius:8px}}
</style></head><body>
<p><a href="/">← SPARQL Explorer</a></p>
<h1>{title}</h1><div class="uri">{uri}</div>
<div class="formats">Other formats: <a href="{path}.ttl">Turtle</a><a href="{path}.nt">N-Triples</a>
<a href="{path}.rdf">RDF/XML</a><a href="{path}.jsonld">JSON-LD</a></div>
{image}
<table><tr><th>Property</th><th>Value</th></tr>{rows}</table>
{incoming}
</body></html>"""


def render_node(node) -> str:
    if isinstance(node, Literal):
        return f'<span class="lit">{html.escape(str(node))}</span>'
    if isinstance(node, BNode):
        return "_:" + html.escape(str(node))
    ext = "" if str(node).startswith(BASE_URI) else ' class="ext" target="_blank"'
    return f'<a href="{html.escape(href(node))}"{ext} title="{html.escape(str(node))}">' \
           f'{html.escape(label_of(node))}</a>'


def html_page(uri: URIRef, g: Graph) -> str:
    rows = "".join(f'<tr><td class="p">{html.escape(shorten(str(p)))}</td><td>{render_node(o)}</td></tr>'
                   for p, o in sorted(g.predicate_objects(uri), key=lambda t: (str(t[0]), str(t[1]))))
    inc = sorted(((s, p) for s, p in g.subject_predicates(uri)), key=lambda t: (str(t[1]), str(t[0])))
    incoming = ""
    if inc:
        incoming = "<h2>Referenced by</h2><table><tr><th>Property</th><th>Subject</th></tr>" + "".join(
            f'<tr><td class="p">{html.escape(shorten(str(p)))}</td><td>{render_node(s)}</td></tr>'
            for s, p in inc) + "</table>"
    img = G.value(uri, URIRef("http://xmlns.com/foaf/0.1/depiction"))
    image = f'<p><img src="{html.escape(str(img))}" alt=""></p>' if img else ""
    path = href(uri)
    return RESOURCE_PAGE.format(title=html.escape(label_of(uri)), uri=html.escape(str(uri)),
                                path=html.escape(path), rows=rows, incoming=incoming, image=image)


def serve_resource(uri: URIRef, graph_fn):
    path = request.path
    for suffix, mime in SUFFIX_FORMATS.items():
        if path.endswith(suffix):
            return rdf_response(graph_fn(), mime)
    # Browsers ask for text/html explicitly; generic clients (Accept: */*) get Turtle.
    mime = negotiate(["text/html"] + list(RDF_FORMATS), "text/turtle")
    if mime == "text/html":
        return Response(html_page(uri, graph_fn()), mimetype="text/html", headers={"Vary": "Accept"})
    if mime not in RDF_FORMATS:
        return error(f"Not acceptable: {mime}", 406)
    return rdf_response(graph_fn(), mime)


@app.route("/data/<path:local>")
def resource(local):
    for suffix in SUFFIX_FORMATS:
        if local.endswith(suffix):
            local = local[: -len(suffix)]
            break
    uri = DATA[local]
    if (uri, None, None) not in G and (None, None, uri) not in G:
        abort(404)
    return serve_resource(uri, lambda: describe(uri))


@app.route("/ontology")
@app.route("/ontology<suffix>")
def ontology(suffix=""):
    if suffix and suffix not in SUFFIX_FORMATS:
        abort(404)
    if not suffix and negotiate(["text/html"] + list(RDF_FORMATS), "text/turtle") == "text/html":
        # human-readable list of classes and properties
        rows = []
        for s in sorted(set(ONTOLOGY.subjects(RDF.type, None)), key=str):
            if not isinstance(s, URIRef):
                continue
            kinds = ", ".join(sorted(shorten(str(t)) for t in ONTOLOGY.objects(s, RDF.type)))
            comment = ONTOLOGY.value(s, RDFS.comment) or ""
            rows.append(f'<tr><td class="p" id="{html.escape(str(s).split("#")[-1])}">'
                        f'{html.escape(shorten(str(s)))}</td><td>{html.escape(kinds)}</td>'
                        f'<td>{html.escape(str(comment))}</td></tr>')
        body = RESOURCE_PAGE.format(title="Football Ontology", uri=html.escape(BASE_URI + "ontology"),
                                    path="/ontology", image="", incoming="",
                                    rows="".join(rows))
        body = body.replace("<th>Property</th><th>Value</th>",
                            "<th>Term</th><th>Type</th><th>Comment</th>")
        return Response(body, mimetype="text/html")
    mime = SUFFIX_FORMATS.get(suffix) or negotiate(list(RDF_FORMATS), "text/turtle")
    return rdf_response(ONTOLOGY, mime if mime in RDF_FORMATS else "text/turtle")


@app.route("/void")
@app.route("/.well-known/void")
@app.route("/data")
def void():
    vg = rebase(bind_prefixes(Graph()).parse(VOID_TTL, format="turtle"))
    mime = negotiate(list(RDF_FORMATS), "text/turtle")
    return rdf_response(vg, mime if mime in RDF_FORMATS else "text/turtle")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "triples": TRIPLE_COUNT})


@app.route("/dump/<name>")
def dump(name):
    allowed = {"football_linked.ttl": LINKED_DIR, "football_linked.nt": LINKED_DIR,
               "void.ttl": LINKED_DIR, "football_data.ttl": RDF_DIR, "football_data.nt": RDF_DIR,
               "football.ttl": ONTO_TTL.parent}
    if name not in allowed:
        abort(404)
    mime = "application/n-triples" if name.endswith(".nt") else "text/turtle"
    return send_from_directory(allowed[name], name, mimetype=mime, as_attachment=False)


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    if os.environ.get("NO_BROWSER") != "1":
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"\n  Web UI          → http://127.0.0.1:{port}/")
    print(f"  SPARQL endpoint → http://127.0.0.1:{port}/sparql")
    print(f"  Example URI     → http://127.0.0.1:{port}/data/player/erling_haaland")
    print("  Press Ctrl+C to stop.\n")
    app.run(host=host, port=port, debug=False)
