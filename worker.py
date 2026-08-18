#!/usr/bin/env python3
"""SAMPLE SIZE - schedule worker (BALLDONTLIE API).

BALLDONTLIE is a real developer API built to be called from servers, so
it won't IP-block a GitHub Actions runner the way ESPN / NBA.com do.
Needs a free API key, provided via the BDL_KEY environment variable
(stored as a GitHub Actions secret). Standard library only.

Default: today's games through the next 3 days.
Set GAME_DATE=YYYYMMDD to target a specific day (great for testing).
"""
import json
import os
import sys
import urllib.request
import urllib.parse
import datetime
import pathlib

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = datetime.timezone(datetime.timedelta(hours=-5))

BASE = "https://api.balldontlie.io/v1/games"
KEY = os.environ.get("BDL_KEY", "").strip()


def api(params):
    q = urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(BASE + "?" + q, headers={"Authorization": KEY})
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
    if "T" in s:            # a scheduled ISO datetime
        return "pre"
    if any(k in s for k in ["Qtr", "Quarter", "Half", "OT", "End"]):
        return "in"
    return "pre"


def one(g):
    at = g.get("visitor_team", {}) or {}
    ht = g.get("home_team", {}) or {}
    st = g.get("status", "")
    iso = g.get("datetime") or (st if "T" in str(st) else None)
    if not iso and g.get("date"):
        iso = g["date"] + "T23:30:00Z"   # approx tip if only a date is given
    return {
        "ha": at.get("abbreviation", ""), "away": at.get("full_name", ""), "arec": "", "arank": "",
        "hh": ht.get("abbreviation", ""), "home": ht.get("full_name", ""), "hrec": "", "hrank": "",
        "tip": to_ms(iso),
        "channel": "League Pass", "national": False,
        "status": status_of(g),
        "injuries": [], "lineups": {"away": [], "home": []},
        "players": [], "ref": {}, "h2h": "",
    }


def main():
    arg = (os.environ.get("GAME_DATE") or (sys.argv[1] if len(sys.argv) > 1 else "")).strip()
    err = None
    games = []
    mode = "today+3"
    try:
        if not KEY:
            raise RuntimeError("BDL_KEY secret is not set")
        if arg:
            d = datetime.datetime.strptime(arg, "%Y%m%d").date().isoformat()
            data = api({"dates[]": [d], "per_page": 100})
            mode = arg
        else:
            today = datetime.datetime.now(ET).date()
            data = api({"start_date": today.isoformat(),
                        "end_date": (today + datetime.timedelta(days=3)).isoformat(),
                        "per_page": 100})
        games = [one(x) for x in data.get("data", [])]
        games.sort(key=lambda g: g["tip"] or 0)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "BALLDONTLIE", "mode": mode, "count": len(games), "error": err, "games": games,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/today.json").write_text(json.dumps(out, indent=2))
    print(f"wrote {len(games)} games" + (f" (error {err})" if err else ""))


if __name__ == "__main__":
    main()
