#!/usr/bin/env python3
"""SAMPLE SIZE - schedule worker.

Pulls the NBA schedule from the league's own public CDN feed (no API key,
cloud-friendly) and writes data/today.json for the app to read.
Standard library only.

Default: today's games through the next 3 days. If none (offseason), it
previews the nearest real slate so you can see live data flowing.
Set GAME_DATE=YYYYMMDD (env var or first CLI arg) to target a specific day.
"""
import json
import os
import sys
import urllib.request
import datetime
import pathlib

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = datetime.timezone(datetime.timedelta(hours=-5))

SCHED_URLS = [
    "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json",
    "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
]
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/122.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}


def fetch():
    last = None
    for url in SCHED_URLS:
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            last = e
    raise last


def to_ms(iso):
    if not iso:
        return None
    try:
        dt = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def names(lst):
    out = []
    for x in (lst or []):
        n = x.get("broadcasterDisplay") or x.get("broadcasterAbbreviation")
        if n:
            out.append(n)
    return list(dict.fromkeys(out))


def one(g):
    at = g.get("awayTeam", {}) or {}
    ht = g.get("homeTeam", {}) or {}
    b = g.get("broadcasters") or {}
    natl = names(b.get("nationalTvBroadcasters"))
    home_tv = names(b.get("homeTvBroadcasters"))
    if natl:
        channel, national = " \u00b7 ".join(natl), True
    else:
        channel = " \u00b7 ".join(home_tv) if home_tv else "Local \u00b7 League Pass"
        national = False
    status = {1: "pre", 2: "in", 3: "post"}.get(g.get("gameStatus", 1), "pre")

    def seed(t):
        s = t.get("seed")
        return str(s) if s else ""

    def rec(t):
        return f"{t.get('wins', 0)}-{t.get('losses', 0)}"

    def nm(t):
        return f"{t.get('teamCity', '')} {t.get('teamName', '')}".strip()

    return {
        "ha": at.get("teamTricode", ""), "away": nm(at), "arec": rec(at), "arank": seed(at),
        "hh": ht.get("teamTricode", ""), "home": nm(ht), "hrec": rec(ht), "hrank": seed(ht),
        "tip": to_ms(g.get("gameDateTimeUTC") or g.get("gameDateUTC")),
        "channel": channel, "national": national, "status": status,
        "injuries": [], "lineups": {"away": [], "home": []},
        "players": [], "ref": {}, "h2h": "",
    }


def gd_date(gd):
    try:
        return datetime.datetime.strptime(gd.get("gameDate", "").split(" ")[0], "%m/%d/%Y").date()
    except Exception:
        return None


def main():
    arg = (os.environ.get("GAME_DATE") or (sys.argv[1] if len(sys.argv) > 1 else "")).strip()
    err = None
    games = []
    season = ""
    total = 0
    mode = "today+3"
    try:
        data = fetch()
        ls = data.get("leagueSchedule", {})
        season = ls.get("seasonYear", "")
        dates = ls.get("gameDates", []) or []
        total = sum(len(d.get("games", []) or []) for d in dates)
        if arg:
            target = datetime.datetime.strptime(arg, "%Y%m%d").date()
            for gd in dates:
                if gd_date(gd) == target:
                    games += [one(x) for x in gd.get("games", [])]
            mode = arg
        else:
            today = datetime.datetime.now(ET).date()
            end = today + datetime.timedelta(days=3)
            for gd in dates:
                d = gd_date(gd)
                if d and today <= d <= end:
                    games += [one(x) for x in gd.get("games", [])]
            if not games:
                dated = [(gd_date(gd), gd) for gd in dates if gd_date(gd) and gd.get("games")]
                upcoming = sorted([x for x in dated if x[0] >= today])
                picks = upcoming[:3] if upcoming else sorted(dated, reverse=True)[:3]
                for _, gd in picks:
                    games += [one(x) for x in gd.get("games", [])]
                if games:
                    mode = "preview (nearest slate - offseason now)"
        games.sort(key=lambda g: g["tip"] or 0)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "NBA", "season": season, "scheduleGames": total,
        "mode": mode, "count": len(games), "error": err, "games": games,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/today.json").write_text(json.dumps(out, indent=2))
    print(f"season {season} · {total} scheduled · wrote {len(games)} games" + (f" (error {err})" if err else ""))


if __name__ == "__main__":
    main()
