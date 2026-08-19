#!/usr/bin/env python3
"""SAMPLE SIZE - standings worker.

Free BALLDONTLIE gives games but not standings, so we compute them:
tally every final score into W-L records, then rank each conference.
Writes data/standings.json (records + seeds + East/West tables).
Runs on its own daily schedule since it pages the whole season.

SEASON env var (or first CLI arg) overrides the season; default is the
current NBA season (or the last completed one during the offseason).
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import datetime
import pathlib

BASE = "https://api.balldontlie.io/v1/games"
KEY = os.environ.get("BDL_KEY", "").strip()


def api(params, cursor=None):
    p = dict(params)
    if cursor:
        p["cursor"] = cursor
    q = urllib.parse.urlencode(p, doseq=True)
    req = urllib.request.Request(BASE + "?" + q, headers={"Authorization": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def all_games(season):
    out, cursor, pages = [], None, 0
    while True:
        data = api({"seasons[]": [season], "per_page": 100}, cursor)
        out += data.get("data", [])
        cursor = (data.get("meta", {}) or {}).get("next_cursor")
        pages += 1
        if not cursor or pages >= 25:
            break
        time.sleep(13)  # stay under the free 5 req/min limit
    return out


def compute(games):
    rec = {}  # abbr -> {w,l,conf,nm}
    for g in games:
        hs, vs = g.get("home_team_score"), g.get("visitor_team_score")
        if "Final" not in str(g.get("status", "")) or hs is None or vs is None:
            continue
        if hs == 0 and vs == 0:
            continue
        ht, at = g.get("home_team", {}) or {}, g.get("visitor_team", {}) or {}
        for t in (ht, at):
            ab = t.get("abbreviation")
            if ab and ab not in rec:
                rec[ab] = {"w": 0, "l": 0, "conf": t.get("conference", ""), "nm": t.get("name", "")}
        ha, aa = ht.get("abbreviation"), at.get("abbreviation")
        if not ha or not aa:
            continue
        if hs > vs:
            rec[ha]["w"] += 1; rec[aa]["l"] += 1
        elif vs > hs:
            rec[aa]["w"] += 1; rec[ha]["l"] += 1

    def pct(v):
        tot = v["w"] + v["l"]
        return v["w"] / tot if tot else 0

    east, west, byTeam = [], [], {}
    for conf, lst in (("East", east), ("West", west)):
        teams = [(ab, v) for ab, v in rec.items() if v["conf"] == conf]
        teams.sort(key=lambda kv: (pct(kv[1]), kv[1]["w"]), reverse=True)
        for i, (ab, v) in enumerate(teams, 1):
            lst.append({"r": i, "ab": ab, "nm": v["nm"], "w": v["w"], "l": v["l"]})
            byTeam[ab] = {"w": v["w"], "l": v["l"], "seed": i, "conf": conf}
    return east, west, byTeam


def main():
    now = datetime.datetime.now()
    default_season = now.year if now.month >= 10 else now.year - 1
    season = int(os.environ.get("SEASON") or (sys.argv[1] if len(sys.argv) > 1 else default_season))
    err, east, west, byTeam = None, [], [], {}
    try:
        if not KEY:
            raise RuntimeError("BDL_KEY not set")
        east, west, byTeam = compute(all_games(season))
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "season": season, "error": err, "east": east, "west": west, "byTeam": byTeam,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/standings.json").write_text(json.dumps(out, indent=2))
    print(f"season {season}: {len(east)} East, {len(west)} West" + (f" (error {err})" if err else ""))


if __name__ == "__main__":
    main()
