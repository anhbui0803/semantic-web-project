#!/usr/bin/env python3
"""
transform.py — Step 3 of Topic 1: convert the raw CSV data into RDF that
meets the 4-star Linked Data standard.

    ★      open licence, on the web            (licence declared in VoID)
    ★★     machine-readable structured data
    ★★★    non-proprietary format              (RDF: Turtle + N-Triples)
    ★★★★   URIs to denote things               (every entity gets an HTTP URI
                                                 under http://semantic-football.org/data/)

Deliberately contains NO links to external datasets: those are the 5th star
and are added by link.py. This keeps the 4★ and 5★ outputs clearly separated
(compare data/rdf/ with data/linked/).

Output: data/rdf/football_data.ttl and data/rdf/football_data.nt
"""

from __future__ import annotations

import re
import unicodedata

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DC, FOAF, OWL, RDF, RDFS, XSD

from common import (DATA, ONTO, ONTOLOGY_URI, DATASET_URI, RDF_DIR, RDF_TTL,
                    bind_prefixes, read_csv)

POSITIONS = {
    "Goalkeeper": ONTO.Goalkeeper,
    "Defender":   ONTO.Defender,
    "Midfielder": ONTO.Midfielder,
    "Forward":    ONTO.Forward,
}


def slug(text: str) -> str:
    """'Nott'm Forest' → 'nottm_forest', 'Jürgen' → 'jurgen'."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def uri(path: str) -> URIRef:
    return DATA[path]


def add_label(g: Graph, node: URIRef, text: str, lang: str | None = "en"):
    """onto:name for our own queries + rdfs:label for generic LOD tools."""
    g.add((node, ONTO.name, Literal(text)))
    g.add((node, RDFS.label, Literal(text, lang=lang)))


def add_int(g, node, prop, value):
    if value not in (None, ""):
        g.add((node, prop, Literal(int(value), datatype=XSD.integer)))


def add_decimal(g, node, prop, value):
    if value not in (None, ""):
        g.add((node, prop, Literal(str(value), datatype=XSD.decimal)))


def add_date(g, node, prop, value):
    if value:
        g.add((node, prop, Literal(value, datatype=XSD.date)))


class Transformer:
    def __init__(self):
        self.g = bind_prefixes(Graph())
        self.g.add((URIRef(DATASET_URI), OWL.imports, URIRef(ONTOLOGY_URI)))
        self.known_cities: set[str] = set()

    # ── helpers that create shared resources ─────────────────
    def city(self, name: str, country: str = "") -> URIRef:
        node = uri(f"city/{slug(name)}")
        if name not in self.known_cities:
            self.known_cities.add(name)
            self.g.add((node, RDF.type, ONTO.City))
            add_label(self.g, node, name)
            if country:
                self.g.add((node, ONTO.country, Literal(country)))
        return node

    def nationality(self, label: str) -> URIRef:
        node = uri(f"nationality/{slug(label)}")
        if (node, RDF.type, ONTO.Nationality) not in self.g:
            self.g.add((node, RDF.type, ONTO.Nationality))
            add_label(self.g, node, label)
        return node

    # ── loaders ──────────────────────────────────────────────
    def load_cities(self):
        rows = read_csv("cities.csv")
        for row in rows:
            self.city(row["name"], row.get("country", ""))
        print(f"  Loaded {len(rows)} cities")

    def load_nationalities(self):
        rows = read_csv("nationalities.csv")
        for row in rows:
            self.nationality(row["label"])
        print(f"  Loaded {len(rows)} nationalities")

    def load_leagues(self):
        rows = read_csv("leagues.csv")
        for row in rows:
            league = uri(f"league/{row['league_id']}")
            self.g.add((league, RDF.type, ONTO.League))
            add_label(self.g, league, row["name"])
            self.g.add((league, ONTO.shortName, Literal(row["short_name"])))
            self.g.add((league, ONTO.country, Literal(row["country"])))
            self.g.add((league, DC.identifier, Literal(row["league_id"])))
            add_int(self.g, league, ONTO.founded, row.get("founded"))
        print(f"  Loaded {len(rows)} leagues")

    def load_seasons(self):
        rows = read_csv("seasons.csv")
        for row in rows:
            season = uri(f"season/{row['season_id']}")
            self.g.add((season, RDF.type, ONTO.Season))
            add_label(self.g, season, f"Premier League {row['label']}")
            self.g.add((season, ONTO.seasonLabel, Literal(row["label"])))
            self.g.add((season, DC.identifier, Literal(row["season_id"])))
            self.g.add((uri(f"league/{row['league_id']}"), ONTO.hasSeason, season))
        print(f"  Loaded {len(rows)} seasons")

    def load_stadiums(self):
        rows = read_csv("stadiums.csv")
        for row in rows:
            stadium = uri(f"stadium/{row['stadium_id']}")
            self.g.add((stadium, RDF.type, ONTO.Stadium))
            add_label(self.g, stadium, row["name"])
            self.g.add((stadium, ONTO.city, Literal(row["city"])))
            self.g.add((stadium, ONTO.country, Literal(row["country"])))
            self.g.add((stadium, ONTO.locatedIn, self.city(row["city"], row["country"])))
            self.g.add((stadium, DC.identifier, Literal(row["stadium_id"])))
            add_int(self.g, stadium, ONTO.capacity, row.get("capacity"))
            add_int(self.g, stadium, ONTO.opened, row.get("opened"))
        print(f"  Loaded {len(rows)} stadiums")

    def _club_common(self, row: dict) -> URIRef:
        club = uri(f"club/{row['club_id']}")
        self.g.add((club, RDF.type, ONTO.FootballClub))
        add_label(self.g, club, row["name"])
        self.g.add((club, ONTO.shortName, Literal(row["short_name"])))
        self.g.add((club, DC.identifier, Literal(row["club_id"])))
        if row.get("city"):
            self.g.add((club, ONTO.city, Literal(row["city"])))
            self.g.add((club, ONTO.locatedIn, self.city(row["city"], row.get("country", ""))))
        if row.get("country"):
            self.g.add((club, ONTO.country, Literal(row["country"])))
        return club

    def load_clubs(self):
        rows = read_csv("clubs.csv")
        for row in rows:
            club = self._club_common(row)
            add_int(self.g, club, ONTO.founded, row.get("founded"))
            if row.get("stadium_id"):
                self.g.add((club, ONTO.hasStadium, uri(f"stadium/{row['stadium_id']}")))
            # Only these clubs are tracked as Premier League participants.
            self.g.add((club, ONTO.participatesIn, uri("league/premier_league")))
        print(f"  Loaded {len(rows)} Premier League clubs")

    def load_other_clubs(self):
        """Clubs that only appear as the origin/destination of a transfer."""
        rows = read_csv("other_clubs.csv")
        for row in rows:
            self._club_common(row)
        print(f"  Loaded {len(rows)} other clubs (transfer counterparts)")

    def load_managers(self):
        rows = read_csv("managers.csv")
        for row in rows:
            manager = uri(f"manager/{row['manager_id']}")
            self.g.add((manager, RDF.type, ONTO.Manager))
            add_label(self.g, manager, row["name"], lang=None)
            self.g.add((manager, FOAF.name, Literal(row["name"])))
            self.g.add((manager, DC.identifier, Literal(row["manager_id"])))
            add_date(self.g, manager, ONTO.dateOfBirth, row.get("date_of_birth"))
            if row.get("nationality"):
                self.g.add((manager, ONTO.hasNationality, self.nationality(row["nationality"])))
            if row.get("club_id"):
                self.g.add((uri(f"club/{row['club_id']}"), ONTO.managedBy, manager))
        print(f"  Loaded {len(rows)} managers")

    def load_players(self):
        rows = read_csv("players.csv")
        for row in rows:
            player = uri(f"player/{row['player_id']}")
            self.g.add((player, RDF.type, ONTO.Player))
            add_label(self.g, player, row["name"], lang=None)
            self.g.add((player, FOAF.name, Literal(row["name"])))
            self.g.add((player, DC.identifier, Literal(row["player_id"])))
            add_date(self.g, player, ONTO.dateOfBirth, row.get("date_of_birth"))
            if row.get("nationality"):
                self.g.add((player, ONTO.hasNationality, self.nationality(row["nationality"])))
            if row.get("position") in POSITIONS:
                self.g.add((player, ONTO.hasPosition, POSITIONS[row["position"]]))
            add_decimal(self.g, player, ONTO.height, row.get("height_cm"))
            if row.get("club_id"):
                self.g.add((player, ONTO.playsFor, uri(f"club/{row['club_id']}")))
        print(f"  Loaded {len(rows)} players")

    def load_matches(self):
        rows = read_csv("matches.csv")
        short = {r["club_id"]: r["short_name"] for r in read_csv("clubs.csv")}
        for row in rows:
            match = uri(f"match/{row['match_id']}")
            home, away = uri(f"club/{row['home_team']}"), uri(f"club/{row['away_team']}")
            hs, as_ = int(row["home_score"]), int(row["away_score"])
            self.g.add((match, RDF.type, ONTO.Match))
            self.g.add((match, DC.identifier, Literal(row["match_id"])))
            self.g.add((match, ONTO.homeTeam, home))
            self.g.add((match, ONTO.awayTeam, away))
            add_date(self.g, match, ONTO.matchDate, row["match_date"])
            add_int(self.g, match, ONTO.matchWeek, row["match_week"])
            add_int(self.g, match, ONTO.homeScore, hs)
            add_int(self.g, match, ONTO.awayScore, as_)
            # derived fact: winner (no onto:wonBy triple for a draw)
            if hs != as_:
                self.g.add((match, ONTO.wonBy, home if hs > as_ else away))
            self.g.add((match, ONTO.partOfLeague, uri(f"league/{row['league_id']}")))
            self.g.add((match, ONTO.partOfSeason, uri(f"season/{row['season_id']}")))
            h = short.get(row["home_team"], row["home_team"])
            a = short.get(row["away_team"], row["away_team"])
            self.g.add((match, RDFS.label,
                        Literal(f"{h} {hs}-{as_} {a} ({row['match_date']})", lang="en")))
        print(f"  Loaded {len(rows)} matches")

    def load_goals(self):
        rows = read_csv("goals.csv")
        for row in rows:
            goal = uri(f"goal/{row['goal_id']}")
            self.g.add((goal, RDF.type, ONTO.Goal))
            self.g.add((goal, DC.identifier, Literal(row["goal_id"])))
            self.g.add((goal, RDFS.label, Literal(f"Goal {row['goal_id']} ({row['minute']}" + (f"+{row['added_time']}" if row.get('added_time') not in (None, '', '0') else '') + "')", lang="en")))
            self.g.add((goal, ONTO.scoredInMatch, uri(f"match/{row['match_id']}")))
            self.g.add((goal, ONTO.scoredForTeam, uri(f"club/{row['team_id']}")))
            add_int(self.g, goal, ONTO.goalMinute, row["minute"])
            if row.get("added_time") not in (None, "", "0"):
                add_int(self.g, goal, ONTO.addedTime, row["added_time"])
            self.g.add((goal, ONTO.isPenalty,
                        Literal(row["is_penalty"].lower() == "true", datatype=XSD.boolean)))
            self.g.add((goal, ONTO.isOwnGoal,
                        Literal(row["is_own_goal"].lower() == "true", datatype=XSD.boolean)))
            if row.get("scorer_id"):
                self.g.add((goal, ONTO.scoredBy, uri(f"player/{row['scorer_id']}")))
        print(f"  Loaded {len(rows)} goals")

    def load_transfers(self):
        rows = read_csv("transfers.csv")
        for row in rows:
            transfer = uri(f"transfer/{row['transfer_id']}")
            self.g.add((transfer, RDF.type, ONTO.Transfer))
            self.g.add((transfer, DC.identifier, Literal(row["transfer_id"])))
            self.g.add((transfer, RDFS.label, Literal(
                f"Transfer of {row['player_id']}: {row['from_club']} → {row['to_club']}", lang="en")))
            self.g.add((transfer, ONTO.transferredPlayer, uri(f"player/{row['player_id']}")))
            self.g.add((transfer, ONTO.fromClub, uri(f"club/{row['from_club']}")))
            self.g.add((transfer, ONTO.toClub, uri(f"club/{row['to_club']}")))
            add_date(self.g, transfer, ONTO.transferDate, row.get("transfer_date"))
            add_decimal(self.g, transfer, ONTO.transferFee, row.get("fee_million_gbp"))
            if row.get("season_id"):
                self.g.add((transfer, ONTO.inSeason, uri(f"season/{row['season_id']}")))
        print(f"  Loaded {len(rows)} transfers")

    def run(self) -> Graph:
        self.load_cities()
        self.load_nationalities()
        self.load_leagues()
        self.load_seasons()
        self.load_stadiums()
        self.load_clubs()
        self.load_other_clubs()
        self.load_managers()
        self.load_players()
        self.load_matches()
        self.load_goals()
        self.load_transfers()
        return self.g


def main():
    print("=== Step 3 — Transforming raw CSV data to RDF (4★ Linked Data) ===\n")
    g = Transformer().run()
    RDF_DIR.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(RDF_TTL), format="turtle")
    g.serialize(destination=str(RDF_TTL.with_suffix(".nt")), format="nt", encoding="utf-8")
    print(f"\n✓ {len(g)} triples → {RDF_TTL}")
    print(f"✓ N-Triples        → {RDF_TTL.with_suffix('.nt')}")


if __name__ == "__main__":
    main()
