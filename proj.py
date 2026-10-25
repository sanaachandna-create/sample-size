#!/usr/bin/env python3
"""SAMPLE SIZE - projections engine (BALLDONTLIE ALL-STAR tier).

Computes each player's season averages AND the noise around them (per-game
standard deviation + sample size) from the /stats endpoint, then builds
projected lines for the common PrizePicks markets.

The whole point of "SAMPLE SIZE": early-season averages come off 1-3 games,
so they LIE. This worker ships the sample size (n) and the spread (sd) next
to every projection so the app can tell you how much to trust it.

Writes data/proj.json. Never fatal: if /stats errors or the tier is missing,
it writes an 'error' and an empty player list; the games worker is untouched.

Season: BALLDONTLIE keys a season by its starting year. The 2026-27 season
is season=2026. Override with SEASON=YYYY if needed.
"""
import json
import os
import sys
import math
import time
import datetime
import pathlib
import urllib.request
import urllib.parse

STATS_URL = "https://api.balldontlie.io/v1/stats"
KEY = os.environ.get("BDL_KEY", "").strip()

# current season start-year; 2026-27 season -> 2026
DEFAULT_SEASON = 2026
MIN_MINUTES = 10.0     # ignore deep-bench noise
TOP_PER_TEAM = 12      # cap roster depth we keep per team


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
    """BDL minutes come as '34', '34:12', '', or None -> float minutes."""
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
    """Sample standard deviation; 0 when fewer than 2 games."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def r1(x):
    return round(x, 1)


def fetch_season_stats(season):
    """Pull every box line for the season, grouped per player id."""
    by_player = {}
    cursor, pages, err = None, 0, None
    try:
        while True:
            data = api(STATS_URL, {"seasons[]": [season], "per_page": 100}, cursor)
            rows = data.get("data", [])
            for s in rows:
                pl = s.get("player") or {}
                pid = pl.get("id")
                if not pid:
                    continue
                tm = s.get("team") or {}
                rec = by_player.setdefault(pid, {
                    "id": pid,
                    "name": (str(pl.get("first_name", "")) + " " + str(pl.get("last_name", ""))).strip(),
                    "team": tm.get("abbreviation", "") or "",
                    "g": [],   # per-game raw rows we care about
                })
                # keep the most recent team we see for the player
                if tm.get("abbreviation"):
                    rec["team"] = tm["abbreviation"]
                mn = to_min(s.get("min"))
                if mn <= 0:
                    continue  # DNP / did not play — not a real sample point
                rec["g"].append({
                    "min": mn,
                    "pts": s.get("pts") or 0,
                    "reb": s.get("reb") or 0,
                    "ast": s.get("ast") or 0,
                    "stl": s.get("stl") or 0,
                    "blk": s.get("blk") or 0,
                    "tov": s.get("turnover") or 0,
                    "fg3m": s.get("fg3m") or 0,
                })
            cursor = (data.get("meta", {}) or {}).get("next_cursor")
            pages += 1
            # generous page cap; early season is small, keeps us well under limits
            if not cursor or pages >= 60:
                break
            time.sleep(1.1)   # 60 req/min ALL-STAR -> ~55/min, safe
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    return by_player, err


def build_player(rec):
    games = rec["g"]
    n = len(games)
    if not n:
        return None
    col = lambda k: [g[k] for g in games]  # noqa: E731
    mins = col("min")
    if mean(mins) < MIN_MINUTES:
        return None

    def stat(k):
        xs = col(k)
        return {"m": r1(mean(xs)), "sd": r1(stdev(xs))}

    pts, reb, ast = stat("pts"), stat("reb"), stat("ast")
    stl, blk, tov, fg3m = stat("stl"), stat("blk"), stat("tov"), stat("fg3m")

    # combined-market means are additive; sd combined ~ sqrt(sum of variances)
    # (ignores correlation — a deliberate, slightly-conservative v1 estimate)
    def combo(*parts):
        m = sum(p["m"] for p in parts)
        sd = math.sqrt(sum(p["sd"] ** 2 for p in parts))
        return {"m": r1(m), "sd": r1(sd)}

    # PrizePicks fantasy score: pts + 1.2*reb + 1.5*ast + 3*stl + 3*blk - tov
    fs_vals = [g["pts"] + 1.2 * g["reb"] + 1.5 * g["ast"]
               + 3 * g["stl"] + 3 * g["blk"] - g["tov"] for g in games]

    return {
        "id": rec["id"], "name": rec["name"], "team": rec["team"],
        "gp": n, "min": r1(mean(mins)),
        "stats": {
            "PTS": pts, "REB": reb, "AST": ast,
            "3PM": fg3m, "STL": stl, "BLK": blk, "TOV": tov,
            "PRA": combo(pts, reb, ast),
            "PR": combo(pts, reb), "PA": combo(pts, ast), "RA": combo(reb, ast),
            "BS": combo(blk, stl),
            "FS": {"m": r1(mean(fs_vals)), "sd": r1(stdev(fs_vals))},
        },
    }


def main():
    season = int(os.environ.get("SEASON") or DEFAULT_SEASON)
    err = None
    players = []
    by_team = {}
    try:
        if not KEY:
            raise RuntimeError("BDL_KEY secret is not set")
        raw, err = fetch_season_stats(season)
        for rec in raw.values():
            p = build_player(rec)
            if p:
                players.append(p)
        # rank within team by minutes, keep the top rotation
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
