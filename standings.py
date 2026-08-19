#!/usr/bin/env python3
"""SAMPLE SIZE - standings worker.

Free BALLDONTLIE has no standings endpoint, so we compute them: seed all
30 teams at 0-0 (so the table is complete even before any games), then
tally every final score into W-L and rank each conference.
Writes data/standings.json.

Default season is the current/upcoming NBA season (season year = the year
it starts). SEASON env var (or first CLI arg) overrides it.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import datetime
import pathlib

GAMES_URL = "https://api.balldontlie.io/v1/games"
TEAMS_URL = "https://api.balldontlie.io/v1/teams"
KEY = os.environ.get("BDL_KEY", "").strip()


def get(url, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def all_games(season):
    out, cursor, pages = [], None, 0
    while True:
        p = {"seasons[]": [season], "per_page": 100}
        if cursor:
            p["cursor"] = cursor
        data = get(GAMES_URL, p)
        out += data.get("data", [])
        cursor = (data.get("meta", {}) or {}).get("next_cursor")
        pages += 1
        if not cursor or pages >= 25:
            break
        time.sleep(13)  # stay under the free 5 req/min limit
    return out


def compute(games, teams):
    rec = {}
    for t in teams:
        ab = t.get("abbreviation")
        if ab and t.get("conference") in ("East", "West"):
            rec[ab] = {"w": 0, "l": 0, "conf": t.get("conference"), "nm": t.get("name", "")}
    for g in games:
        hs, vs = g.get("home_team_score"), g.get("visitor_team_score")
        if "Final" not in str(g.get("status", "")) or hs is None or vs is None:
            continue
        if hs == 0 and vs == 0:
            continue
        ha = (g.get("home_team", {}) or {}).get("abbreviation")
        aa = (g.get("visitor_team", {}) or {}).get("abbreviation")
        if ha not in rec or aa not in rec:
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
        teams_c = [(ab, v) for ab, v in rec.items() if v["conf"] == conf]
        teams_c.sort(key=lambda kv: (pct(kv[1]), kv[1]["w"], -ord(kv[0][0])), reverse=True)
        for i, (ab, v) in enumerate(teams_c, 1):
            lst.append({"r": i, "ab": ab, "nm": v["nm"], "w": v["w"], "l": v["l"]})
            byTeam[ab] = {"w": v["w"], "l": v["l"], "seed": i, "conf": conf}
    return east, west, byTeam


def main():
    now = datetime.datetime.now()
    default_season = now.year if now.month >= 7 else now.year - 1
    season = int(os.environ.get("SEASON") or (sys.argv[1] if len(sys.argv) > 1 else default_season))
    err, east, west, byTeam = None, [], [], {}
    try:
        if not KEY:
            raise RuntimeError("BDL_KEY not set")
        teams = get(TEAMS_URL).get("data", [])
        time.sleep(1)
        east, west, byTeam = compute(all_games(season), teams)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "season": season, "label": f"{season}-{str(season+1)[2:]}",
        "error": err, "east": east, "west": west, "byTeam": byTeam,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/standings.json").write_text(json.dumps(out, indent=2))
    print(f"{out['label']}: {len(east)} East, {len(west)} West" + (f" (error {err})" if err else ""))


if __name__ == "__main__":
    main()
