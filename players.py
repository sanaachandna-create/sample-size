#!/usr/bin/env python3
"""SAMPLE SIZE - active player list for the watchlist search.

Pulls every active NBA player (name, team, position) from BALLDONTLIE and
writes data/players.json. Powers the type-ahead watchlist picker in Settings.
Runs daily — rosters change slowly. Never fatal.
"""
import json
import os
import time
import datetime
import pathlib
import urllib.request
import urllib.parse

URL = "https://api.balldontlie.io/v1/players/active"
KEY = os.environ.get("BDL_KEY", "").strip()


def api(cursor=None):
    p = {"per_page": 100}
    if cursor:
        p["cursor"] = cursor
    u = URL + "?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(u, headers={"Authorization": KEY})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def main():
    err = None
    out = []
    try:
        if not KEY:
            raise RuntimeError("BDL_KEY secret is not set")
        cursor, pages = None, 0
        while True:
            d = api(cursor)
            for p in d.get("data", []):
                nm = (str(p.get("first_name", "")) + " " + str(p.get("last_name", ""))).strip()
                tm = (p.get("team") or {}).get("abbreviation", "") or ""
                pos = p.get("position", "") or ""
                if nm:
                    out.append({"id": p.get("id"), "name": nm, "team": tm, "pos": pos})
            cursor = (d.get("meta", {}) or {}).get("next_cursor")
            pages += 1
            if not cursor or pages >= 40:
                break
            time.sleep(1.1)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    out.sort(key=lambda x: x["name"])
    res = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "count": len(out), "error": err, "players": out,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/players.json").write_text(json.dumps(res, indent=2))
    print(f"wrote {len(out)} players" + (f" (error {err})" if err else ""))


if __name__ == "__main__":
    main()
