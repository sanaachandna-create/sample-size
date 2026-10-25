#!/usr/bin/env python3
"""SAMPLE SIZE - projections engine (BALLDONTLIE ALL-STAR tier).

Computes each player's season averages AND the noise around them (per-game
standard deviation + sample size) from the /stats endpoint, then builds
projected lines for the PrizePicks markets BALLDONTLIE can support.

The whole point of "SAMPLE SIZE": early-season averages come off 1-3 games,
so they LIE. This worker ships the sample size (n) and the spread (sd) next
to every projection so the app can tell you how much to trust it.

Markets built (single player):
  PTS REB AST 3PM 3PA 2PM 2PA FGM FGA FTM FTA OREB DREB STL BLK TOV
  + combos PRA PR PA RA BS  + Fantasy (FS)
  + double-double / triple-double hit rates
Not available from BALLDONTLIE: dunks (not in the box score feed).

Writes data/proj.json. Never fatal: if /stats errors or the tier is missing,
it writes an 'error' and an empty player list; the games worker is untouched.

Season: BALLDONTLIE keys a season by its starting year. The 2026-27 season
is season=2026. Override with SEASON=YYYY if needed.
"""
import json
import os
import math
import time
import datetime
import pathlib
import urllib.request
import urllib.parse

STATS_URL = "https://api.balldontlie.io/v1/stats"
KEY = os.environ.get("BDL_KEY", "").strip()

DEFAULT_SEASON = 2026
MIN_MINUTES = 10.0
TOP_PER_TEAM = 14

# per-game raw columns we pull from each box line
RAW = ["pts", "reb", "ast", "stl", "blk", "tov",
       "fgm", "fga", "fg3m", "fg3a", "ftm", "fta", "oreb", "dreb"]


def api(url, params=None, cursor=None):
    p = dict(params or {})
    if cursor:
        p["cursor"] = cursor
    if p:
        url += "?" + urllib.parse.urlencode(p, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": KEY})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def to_min(v):
    if v is None:
        return 0.0
    s = str(v).strip()
    if not s:
        return 0.0
    if ":" in s:
        try:
            m, sec = s.split(":")[:2]
            return int(m) + (int(sec) / 60.0 if sec else 0.0)
        except Exception:  # noqa: BLE001
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def r1(x):
    return round(x, 1)


def team_abbr_map():
    """BDL team id -> tricode, from the free teams endpoint."""
    m = {}
    try:
        for t in api("https://api.balldontlie.io/v1/teams").get("data", []):
            if t.get("id") and t.get("abbreviation"):
                m[t["id"]] = t["abbreviation"]
    except Exception:  # noqa: BLE001
        pass
    return m


def fetch_season_stats(season, id2abbr):
    by_player = {}
    cursor, pages, err = None, 0, None
    try:
        while True:
            data = api(STATS_URL, {"seasons[]": [season], "per_page": 100}, cursor)
            for s in data.get("data", []):
                pl = s.get("player") or {}
                pid = pl.get("id")
                if not pid:
                    continue
                tm = s.get("team") or {}
                rec = by_player.setdefault(pid, {
                    "id": pid,
                    "name": (str(pl.get("first_name", "")) + " " + str(pl.get("last_name", ""))).strip(),
                    "team": tm.get("abbreviation", "") or "",
                    "g": [],
                })
                if tm.get("abbreviation"):
                    rec["team"] = tm["abbreviation"]
                mn = to_min(s.get("min"))
                if mn <= 0:
                    continue
                row = {"min": mn}
                for k in RAW:
                    row[k] = s.get(k) or 0
                # derived two-point splits
                row["twopm"] = row["fgm"] - row["fg3m"]
                row["twopa"] = row["fga"] - row["fg3a"]
                # date + opponent for the trend graph
                game = s.get("game") or {}
                row["d"] = (game.get("date") or "")[:10]
                tid = tm.get("id")
                hid, vid = game.get("home_team_id"), game.get("visitor_team_id")
                opp_id = vid if tid == hid else hid
                row["o"] = id2abbr.get(opp_id, "")
                rec["g"].append(row)
            cursor = (data.get("meta", {}) or {}).get("next_cursor")
            pages += 1
            if not cursor or pages >= 80:
                break
            time.sleep(1.1)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    return by_player, err


# per-game stat -> projection market key
SINGLES = {
    "PTS": "pts", "REB": "reb", "AST": "ast",
    "3PM": "fg3m", "3PA": "fg3a", "2PM": "twopm", "2PA": "twopa",
    "FGM": "fgm", "FGA": "fga", "FTM": "ftm", "FTA": "fta",
    "OREB": "oreb", "DREB": "dreb", "STL": "stl", "BLK": "blk", "TOV": "tov",
}


def build_player(rec):
    games = rec["g"]
    n = len(games)
    if not n:
        return None
    mins = [g["min"] for g in games]
    if mean(mins) < MIN_MINUTES:
        return None

    def stat_from(col):
        return {"m": r1(mean(col)), "sd": r1(stdev(col))}

    stats = {}
    for mkt, raw in SINGLES.items():
        stats[mkt] = stat_from([g[raw] for g in games])

    def combo(*keys):
        m = sum(stats[k]["m"] for k in keys)
        sd = math.sqrt(sum(stats[k]["sd"] ** 2 for k in keys))
        return {"m": r1(m), "sd": r1(sd)}

    stats["PRA"] = combo("PTS", "REB", "AST")
    stats["PR"] = combo("PTS", "REB")
    stats["PA"] = combo("PTS", "AST")
    stats["RA"] = combo("REB", "AST")
    stats["BS"] = combo("BLK", "STL")

    fs_vals = [g["pts"] + 1.2 * g["reb"] + 1.5 * g["ast"]
               + 3 * g["stl"] + 3 * g["blk"] - g["tov"] for g in games]
    stats["FS"] = {"m": r1(mean(fs_vals)), "sd": r1(stdev(fs_vals))}

    # double-double / triple-double hit rate (pts/reb/ast/stl/blk >= 10)
    def doubles(g):
        return sum(1 for v in (g["pts"], g["reb"], g["ast"], g["stl"], g["blk"]) if v >= 10)
    dd = sum(1 for g in games if doubles(g) >= 2)
    td = sum(1 for g in games if doubles(g) >= 3)

    # season rate stats (computed from totals, not avg-of-percentages)
    tot = lambda k: sum(g[k] for g in games)  # noqa: E731
    fga_t, fta_t = tot("fga"), tot("fta")
    tsa = fga_t + 0.44 * fta_t
    rates = {
        "MPG": r1(mean(mins)),
        "FG%": r1(tot("fgm") / fga_t * 100) if fga_t else 0.0,
        "3P%": r1(tot("fg3m") / tot("fg3a") * 100) if tot("fg3a") else 0.0,
        "FT%": r1(tot("ftm") / fta_t * 100) if fta_t else 0.0,
        "TS%": r1(tot("pts") / (2 * tsa) * 100) if tsa else 0.0,
    }

    # last 15 games, oldest->newest, compact raw values for the trend graph
    glog = sorted(games, key=lambda r: r.get("d", ""))[-15:]
    log = [{"d": r.get("d", ""), "o": r.get("o", ""), "mn": round(r["min"], 1),
            "pts": r["pts"], "reb": r["reb"], "ast": r["ast"],
            "fg3m": r["fg3m"], "fg3a": r["fg3a"], "fgm": r["fgm"], "fga": r["fga"],
            "ftm": r["ftm"], "fta": r["fta"], "oreb": r["oreb"], "dreb": r["dreb"],
            "stl": r["stl"], "blk": r["blk"], "tov": r["tov"]} for r in glog]

    return {
        "id": rec["id"], "name": rec["name"], "team": rec["team"],
        "gp": n, "min": r1(mean(mins)),
        "dd": round(dd / n, 2), "td": round(td / n, 2),
        "rates": rates, "stats": stats, "log": log,
    }


def main():
    season = int(os.environ.get("SEASON") or DEFAULT_SEASON)
    err = None
    players = []
    by_team = {}
    try:
        if not KEY:
            raise RuntimeError("BDL_KEY secret is not set")
        id2abbr = team_abbr_map()
        raw, err = fetch_season_stats(season, id2abbr)
        for rec in raw.values():
            p = build_player(rec)
            if p:
                players.append(p)
        players.sort(key=lambda p: (p["team"], -p["min"]))
        kept = []
        for p in players:
            t = p["team"] or "FA"
            lst = by_team.setdefault(t, [])
            if len(lst) < TOP_PER_TEAM:
                lst.append(p["id"])
                kept.append(p)
        players = kept
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "BALLDONTLIE", "season": season,
        "playerCount": len(players), "error": err,
        "byTeam": by_team, "players": players,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/proj.json").write_text(json.dumps(out, indent=2))
    print(f"wrote {len(players)} players across {len(by_team)} teams"
          + (f" (error {err})" if err else ""))


if __name__ == "__main__":
    main()
