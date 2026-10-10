# Data sources

All values in `data/raw/*.csv` were collected by hand from the public sources below
and checked on 2026-10-08. Nothing was estimated or generated. Where a value could not
be confirmed, the cell is left empty instead of guessed.

## Scope

Fifteen 2023-24 Premier League matches: exactly one match for every pair of the six clubs
(Arsenal, Aston Villa, Chelsea, Liverpool, Manchester City, Tottenham Hotspur).
These fifteen matches contain 45 goals, scored by 31 different players.
`players.csv` holds exactly those 31 scorers (own-goal scorers included).

## Per file

| File | What | Source |
|---|---|---|
| `matches.csv` | date, home/away team, score, matchweek | [openfootball/england 2023-24](https://github.com/openfootball/england/blob/master/2023-24/1-premierleague.txt) (public domain); every score re-checked on the 11v11.com match page listed below |
| `goals.csv` | scorer, minute, added time, penalty / own goal, team credited | 11v11.com match pages (list below). Own goals: `scorer_id` is the player who put the ball into his own net, `team_id` the club credited with the goal |
| `players.csv` | date of birth, height, position, national team | English Wikipedia article of each player (infobox). `position` is the first position listed, mapped to GK/DF/MF/FW; wingers are counted as forwards. `nationality` is the senior national team. `club_id` is the club the player scored for in 2023-24 |
| `managers.csv` | date of birth, nationality, club in 2023-24 | English Wikipedia article of each manager |
| `clubs.csv` | year founded, city, ground | English Wikipedia club articles (infobox) |
| `stadiums.csv` | capacity, year opened | English Wikipedia stadium articles (infobox; capacity as listed on 2026-10-08) |
| `transfers.csv` | date, clubs, fee | English Wikipedia player articles, transfer paragraphs. `fee_million_gbp` is the initial fee as reported, without add-ons. Gakpo's fee was only reported as a range (£35.4m–£44.3m), so the cell is empty |
| `leagues.csv`, `seasons.csv` | founding year, season items | English Wikipedia |
| `other_clubs.csv`, `cities.csv`, `nationalities.csv` | reference data for links | Wikipedia / Wikidata / GeoNames |

## External identifiers

* `dbpedia_uri`: `http://dbpedia.org/resource/` + the title of the English Wikipedia article
  (DBpedia resources are named after Wikipedia articles).
* `wikidata_id`: taken from the Wikidata item whose English label matches the entity.
  Left empty where several Wikidata items share the same label and the right one could
  not be confirmed (Rodri, Luis Díaz). `scripts/discover_links.py` can fill these from
  DBpedia's own `owl:sameAs` links when it is run with internet access.
* `geonames_id`: GeoNames "largest cities in United Kingdom" page; not filled for the
  cities outside the UK.

## Match pages (11v11.com)

| ID | Match | Page |
|---|---|---|
| m001 | Arsenal 1-0 Manchester City, 8 Oct 2023 | https://www.11v11.com/matches/arsenal-v-manchester-city-08-october-2023-381625/ |
| m002 | Tottenham 2-1 Liverpool, 30 Sep 2023 | https://www.11v11.com/matches/tottenham-hotspur-v-liverpool-30-september-2023-380865/ |
| m003 | Manchester City 1-1 Liverpool, 25 Nov 2023 | https://www.11v11.com/matches/manchester-city-v-liverpool-25-november-2023-382018/ |
| m004 | Chelsea 2-2 Arsenal, 21 Oct 2023 | https://www.11v11.com/matches/chelsea-v-arsenal-21-october-2023-381632/ |
| m005 | Tottenham 1-2 Aston Villa, 26 Nov 2023 | https://www.11v11.com/matches/tottenham-hotspur-v-aston-villa-26-november-2023-382025/ |
| m006 | Manchester City 3-3 Tottenham, 3 Dec 2023 | https://www.11v11.com/matches/manchester-city-v-tottenham-hotspur-03-december-2023-382403/ |
| m007 | Aston Villa 1-0 Manchester City, 6 Dec 2023 | https://www.11v11.com/matches/aston-villa-v-manchester-city-06-december-2023-382410/ |
| m008 | Aston Villa 1-0 Arsenal, 9 Dec 2023 | https://www.11v11.com/matches/aston-villa-v-arsenal-09-december-2023-382419/ |
| m009 | Liverpool 3-0 Aston Villa, 3 Sep 2023 | https://www.11v11.com/matches/liverpool-v-aston-villa-03-september-2023-380836/ |
| m010 | Liverpool 4-1 Chelsea, 31 Jan 2024 | https://www.11v11.com/matches/liverpool-v-chelsea-31-january-2024-382715/ |
| m011 | Arsenal 3-1 Liverpool, 4 Feb 2024 | https://www.11v11.com/matches/arsenal-v-liverpool-04-february-2024-383143/ |
| m012 | Manchester City 1-1 Chelsea, 17 Feb 2024 | https://www.11v11.com/matches/manchester-city-v-chelsea-17-february-2024-383161/ |
| m013 | Arsenal 2-2 Tottenham, 24 Sep 2023 | https://www.11v11.com/matches/arsenal-v-tottenham-hotspur-24-september-2023-380854/ |
| m014 | Chelsea 0-1 Aston Villa, 24 Sep 2023 | https://www.11v11.com/matches/chelsea-v-aston-villa-24-september-2023-380848/ |
| m015 | Tottenham 1-4 Chelsea, 6 Nov 2023 | https://www.11v11.com/matches/tottenham-hotspur-v-chelsea-06-november-2023-382007/ |
