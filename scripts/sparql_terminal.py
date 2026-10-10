#!/usr/bin/env python3
"""
sparql_terminal.py — Step 5 of Topic 1: interactive SPARQL terminal.

• Loads ontology + 5★ dataset (+ VoID) into an in-memory rdflib Graph,
  or talks to any remote SPARQL endpoint with --endpoint (e.g. the one
  started by app.py, or https://dbpedia.org/sparql).
• REPL with named example queries (read from queries/example_queries.sparql).
• Non-interactive mode for scripts / demos:  -q "<query>"  or  -f file.rq
• Output as an ASCII table, CSV or SPARQL-JSON.

Usage
-----
    python scripts/sparql_terminal.py
    python scripts/sparql_terminal.py --dataset 4star
    python scripts/sparql_terminal.py -q "SELECT ?n WHERE { ?p a onto:Player ; onto:name ?n } LIMIT 5"
    python scripts/sparql_terminal.py -f my_query.rq --format csv
    python scripts/sparql_terminal.py --endpoint http://127.0.0.1:5000/sparql
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rdflib import BNode, Literal, URIRef

from common import PREFIX_BLOCK, load_example_queries, load_graph, shorten

if hasattr(sys.stdout, "reconfigure"):          # Windows consoles: print ★, →, é …
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────
#  Back-ends: local rdflib graph or remote endpoint
# ─────────────────────────────────────────────────────────────
class LocalBackend:
    def __init__(self, dataset: str):
        self.g = load_graph(dataset)
        self.name = f"local {dataset} dataset"

    def size(self) -> str:
        return f"{len(self.g)} triples"

    def query(self, text: str):
        r = self.g.query(PREFIX_BLOCK + "\n" + text)
        if r.type == "SELECT":
            vars_ = [str(v) for v in r.vars]
            rows = [[row[v] for v in r.vars] for row in r]
            return "SELECT", (vars_, rows)
        if r.type == "ASK":
            return "ASK", bool(r.askAnswer)
        return "GRAPH", list(r.graph)


class RemoteBackend:
    def __init__(self, url: str):
        from SPARQLWrapper import JSON, N3, SPARQLWrapper
        self.sw, self.JSON, self.N3 = SPARQLWrapper(url), JSON, N3
        self.sw.setTimeout(30)
        self.name = f"remote endpoint {url}"

    def size(self) -> str:
        try:
            _, (_, rows) = self.query("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }")
            return f"{rows[0][0]} triples"
        except Exception:
            return "unknown size"

    def query(self, text: str):
        from rdflib import Graph
        self.sw.setQuery(PREFIX_BLOCK + "\n" + text)
        qtype = self.sw.queryType
        if qtype in ("CONSTRUCT", "DESCRIBE"):
            self.sw.setReturnFormat(self.N3)
            data = self.sw.query().convert()
            g = Graph().parse(data=data, format="turtle")
            return "GRAPH", list(g)
        self.sw.setReturnFormat(self.JSON)
        res = self.sw.query().convert()
        if "boolean" in res:
            return "ASK", res["boolean"]
        vars_ = res["head"]["vars"]
        rows = []
        for b in res["results"]["bindings"]:
            row = []
            for v in vars_:
                cell = b.get(v)
                if cell is None:
                    row.append(None)
                elif cell["type"] == "uri":
                    row.append(URIRef(cell["value"]))
                elif cell["type"] == "bnode":
                    row.append(BNode(cell["value"]))
                else:
                    row.append(Literal(cell["value"], lang=cell.get("xml:lang"),
                                       datatype=cell.get("datatype")))
            rows.append(row)
        return "SELECT", (vars_, rows)


# ─────────────────────────────────────────────────────────────
#  Output
# ─────────────────────────────────────────────────────────────
def cell_text(val, width: int | None = 80) -> str:
    if val is None:
        return ""
    if isinstance(val, URIRef):
        return shorten(str(val))
    if isinstance(val, BNode):
        return f"_:{val}"
    s = str(val)
    return s[:width] + "…" if width and len(s) > width else s


def print_table(vars_, rows):
    if not rows:
        print("  (no results)")
        return
    table = [[cell_text(c) for c in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in table]) for i, h in enumerate(vars_)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    print(sep)
    print("|" + "|".join(f" {h:<{widths[i]}} " for i, h in enumerate(vars_)) + "|")
    print(sep)
    for r in table:
        print("|" + "|".join(f" {c:<{widths[i]}} " for i, c in enumerate(r)) + "|")
    print(sep)
    print(f"  {len(rows)} row(s)")


def print_csv(vars_, rows):
    import csv
    w = csv.writer(sys.stdout)
    w.writerow(vars_)
    for r in rows:
        w.writerow(["" if c is None else str(c) for c in r])


def print_json(vars_, rows):
    def enc(c):
        if isinstance(c, URIRef):
            return {"type": "uri", "value": str(c)}
        if isinstance(c, BNode):
            return {"type": "bnode", "value": str(c)}
        d = {"type": "literal", "value": str(c)}
        if c.language:
            d["xml:lang"] = c.language
        elif c.datatype:
            d["datatype"] = str(c.datatype)
        return d
    out = {"head": {"vars": vars_},
           "results": {"bindings": [{v: enc(c) for v, c in zip(vars_, r) if c is not None}
                                    for r in rows]}}
    print(json.dumps(out, indent=2, ensure_ascii=False))


def print_graph(triples):
    for s, p, o in sorted(triples, key=lambda t: (str(t[0]), str(t[1]), str(t[2]))):
        print(f"  {cell_text(s)}\n      {cell_text(p)}\n          {cell_text(o, 120)}")
    print(f"\n  {len(triples)} triple(s)")


def run_query(backend, text: str, fmt: str = "table") -> bool:
    try:
        kind, data = backend.query(text.strip())
    except Exception as e:
        print(f"  ERROR: {e}")
        return False
    if kind == "SELECT":
        {"table": print_table, "csv": print_csv, "json": print_json}[fmt](*data)
    elif kind == "ASK":
        print(f"  Result: {data}")
    else:
        print_graph(data)
    return True


# ─────────────────────────────────────────────────────────────
#  REPL
# ─────────────────────────────────────────────────────────────
HELP_TEXT = """
Commands
--------
  :help                Show this help message
  :examples            List all named example queries
  :run <name>          Execute a named example query
  :show <name>         Print the SPARQL text of a named example query
  :file <path>         Execute the query stored in a file (.rq / .sparql)
  :format table|csv|json   Change the output format of SELECT results
  :prefixes            Print the auto-prepended PREFIX block
  :count               Show the size of the dataset
  :quit / :exit        Exit

Ad-hoc queries
--------------
  Type or paste any SPARQL query; a blank line submits it.
  The PREFIX block is added automatically (onto:, data:, club:, player:, owl:, …),
  so you can write e.g.   DESCRIBE player:erling_haaland
"""


def repl(backend, examples, fmt):
    print("\n" + "=" * 60)
    print(" Football Linked Data — SPARQL Terminal")
    print("=" * 60)
    print(f" Connected to: {backend.name} ({backend.size()})")
    print(" Type  :help  for available commands.\n")
    buf: list[str] = []
    while True:
        try:
            line = input("SPARQL> " if not buf else "     …  ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break
        cmd = line.strip()
        if not buf and cmd.startswith(":"):
            name, _, arg = cmd.partition(" ")
            arg = arg.strip()
            if name in (":quit", ":exit", ":q"):
                print("Bye!")
                break
            elif name == ":help":
                print(HELP_TEXT)
            elif name == ":prefixes":
                print(PREFIX_BLOCK)
            elif name == ":count":
                print(f"  {backend.size()}")
            elif name == ":examples":
                print("\n  Named example queries:")
                for k, info in examples.items():
                    net = "  [needs internet]" if "network" in info["requires"] else ""
                    print(f"    {k:<28}  {info['description']}{net}")
                print()
            elif name in (":run", ":show"):
                if arg not in examples:
                    print(f"  Unknown example: '{arg}'. Use :examples to list them.")
                elif name == ":show":
                    print(f"\n  -- {examples[arg]['description']} --\n{examples[arg]['query']}")
                else:
                    print(f"\n  [{arg}] {examples[arg]['description']}\n")
                    run_query(backend, examples[arg]["query"], fmt)
            elif name == ":file":
                try:
                    run_query(backend, Path(arg).read_text(encoding="utf-8"), fmt)
                except OSError as e:
                    print(f"  ERROR: {e}")
            elif name == ":format":
                if arg in ("table", "csv", "json"):
                    fmt = arg
                    print(f"  Output format: {fmt}")
                else:
                    print("  Usage: :format table|csv|json")
            else:
                print(f"  Unknown command {name}. Type :help.")
            continue
        if cmd == "":
            if buf:
                print()
                run_query(backend, "\n".join(buf), fmt)
                print()
                buf = []
            continue
        buf.append(line)


def main():
    ap = argparse.ArgumentParser(description="SPARQL terminal for Football Linked Data")
    ap.add_argument("--dataset", choices=["4star", "5star"], default="5star",
                    help="local dataset to load (default: 5star)")
    ap.add_argument("--endpoint", help="query a remote SPARQL endpoint instead of the local files")
    ap.add_argument("-q", "--query", help="run one query and exit")
    ap.add_argument("-f", "--file", help="run the query in FILE and exit")
    ap.add_argument("-r", "--run", metavar="NAME", help="run a named example query and exit")
    ap.add_argument("--format", choices=["table", "csv", "json"], default="table")
    args = ap.parse_args()

    try:
        backend = RemoteBackend(args.endpoint) if args.endpoint else LocalBackend(args.dataset)
    except FileNotFoundError as e:
        sys.exit(str(e))
    examples = load_example_queries()

    if args.query or args.file or args.run:
        if args.run:
            if args.run not in examples:
                sys.exit(f"Unknown example '{args.run}'. Available: {', '.join(examples)}")
            text = examples[args.run]["query"]
        else:
            text = args.query or Path(args.file).read_text(encoding="utf-8")
        sys.exit(0 if run_query(backend, text, args.format) else 1)
    repl(backend, examples, args.format)


if __name__ == "__main__":
    main()
