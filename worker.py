#!/usr/bin/env python3
"""SAMPLE SIZE - schedule + injuries worker (BALLDONTLIE API).

Games (free tier) + player injuries (ALL-STAR tier). Injuries are fetched
defensively: if the endpoint errors or the account lacks the tier, games
still write normally and 'injuriesError' records why.

Default: today's games through the next 3 days.
Set GAME_DATE=YYYYMMDD to target a specific day.
"""
import json
import os
import sys
import urllib.request
import urllib.parse
import datetime
import pathlib
import time

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = datetime.timezone(datetime.timedelta(hours=-5))

GAMES_URL = "https://api.balldontlie.io/v1/games"
TEAMS_URL = "https://api.balldontlie.io/v1/teams"
INJ_URL = "https://api.balldontlie.io/v1/player_injuries"
KEY = os.environ.get("BDL_KEY", "").strip()


def api(url, params=None, cursor=None):
    p = dict(params or {})
    if cursor:
        p["cursor"] = cursor
    if p:
        url += "?" + urllib.parse.urlencode(p, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def to_ms(iso):
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def status_of(g):
    s = str(g.get("status", "")).strip()
    if "Final" in s:
        return "post"
    if "T" in s:
        return "pre"
    if any(k in s for k in ["Qtr", "Quarter", "Half", "OT", "End"]):
        return "in"
    return "pre"


def norm_status(raw):
    s = (raw or "").lower()
    if "out for season" in s or s == "out":
        return "OUT"
    if "out" in s:
        return "OUT"
    if any(k in s for k in ["day-to-day", "day to day", "game time", "questionable", "probable", "doubtful", "gtd"]):
        return "GTD"
    return (raw or "").upper()[:3] or "INJ"


def one(g):
    at = g.get("visitor_team", {}) or {}
    ht = g.get("home_team", {}) or {}
    st = g.get("status", "")
    iso = g.get("datetime") or (st if "T" in str(st) else None)
    if not iso and g.get("date"):
        iso = g["date"] + "T23:30:00Z"
    return {
        "ha": at.get("abbreviation", ""), "away": at.get("full_name", ""), "arec": "", "arank": "",
        "hh": ht.get("abbreviation", ""), "home": ht.get("full_name", ""), "hrec": "", "hrank": "",
        "tip": to_ms(iso),
        "channel": "League Pass", "national": False,
        "status": status_of(g),
        "hscore": g.get("home_team_score"), "ascore": g.get("visitor_team_score"),
        "period": g.get("period"), "clock": str(g.get("time") or "").strip(),
        "injuries": [], "lineups": {"away": [], "home": []},
        "players": [], "ref": {}, "h2h": "",
    }


def team_abbr_map():
    """id -> tricode, from the free teams endpoint."""
    m = {}
    try:
        for t in api(TEAMS_URL).get("data", []):
            if t.get("id") and t.get("abbreviation"):
                m[t["id"]] = t["abbreviation"]
    except Exception:  # noqa: BLE001
        pass
    return m


def fetch_injuries(id2abbr):
    """Return {tricode: [ {p, st, team, note}, ... ]} and an error string or None."""
    by_team, cursor, pages, err = {}, None, 0, None
    try:
        while True:
            data = api(INJ_URL, {"per_page": 100}, cursor)
            for it in data.get("data", []):
                pl = it.get("player", {}) or {}
                name = (str(pl.get("first_name", "")) + " " + str(pl.get("last_name", ""))).strip() or pl.get("name", "")
                # team can live in several shapes depending on the response
                abbr = ""
                tm = pl.get("team") or it.get("team") or {}
                if isinstance(tm, dict):
                    abbr = tm.get("abbreviation", "")
                if not abbr:
                    tid = pl.get("team_id") or it.get("team_id")
                    abbr = id2abbr.get(tid, "")
                note = it.get("description") or it.get("return_date") or it.get("note") or ""
                st = norm_status(it.get("status") or it.get("injury") or "")
                if name:
                    by_team.setdefault(abbr, []).append(
                        {"p": name, "st": st, "team": abbr, "note": str(note)[:90]})
            cursor = (data.get("meta", {}) or {}).get("next_cursor")
            pages += 1
            if not cursor or pages >= 5:
                break
            time.sleep(13)  # respect 60 req/min on ALL-STAR comfortably
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    return by_team, err


def main():
    arg = (os.environ.get("GAME_DATE") or (sys.argv[1] if len(sys.argv) > 1 else "")).strip()
    err = None
    inj_err = None
    games = []
    mode = "today+3"
    try:
        if not KEY:
            raise RuntimeError("BDL_KEY secret is not set")
        if arg:
            d = datetime.datetime.strptime(arg, "%Y%m%d").date().isoformat()
            data = api(GAMES_URL, {"dates[]": [d], "per_page": 100})
            mode = arg
        else:
            today = datetime.datetime.now(ET).date()
            data = api(GAMES_URL, {"start_date": today.isoformat(),
                                   "end_date": (today + datetime.timedelta(days=3)).isoformat(),
                                   "per_page": 100})
        games = [one(x) for x in data.get("data", [])]
        games.sort(key=lambda g: g["tip"] or 0)

        # injuries (ALL-STAR) — attach to each game's two teams; never fatal
        id2abbr = team_abbr_map()
        time.sleep(1)
        by_team, inj_err = fetch_injuries(id2abbr)
        if by_team:
            for g in games:
                g["injuries"] = (by_team.get(g["ha"], []) + by_team.get(g["hh"], []))
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    total_inj = sum(len(g["injuries"]) for g in games)
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "BALLDONTLIE", "mode": mode, "count": len(games),
        "error": err, "injuriesError": inj_err, "injuryCount": total_inj, "games": games,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/today.json").write_text(json.dumps(out, indent=2))
    print(f"wrote {len(games)} games, {total_inj} injuries"
          + (f" (error {err})" if err else "") + (f" (inj {inj_err})" if inj_err else ""))


if __name__ == "__main__":
    main()
