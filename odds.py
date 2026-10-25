#!/usr/bin/env python3
"""SAMPLE SIZE - team betting engine.

Two layers, same honesty as THE LAB:
  1. A TEAM MODEL built from this season's game results (BALLDONTLIE):
     each team's points-for / points-against per game, league pace baseline.
  2. The MARKET LINES (spread / total / moneyline) from The Odds API free tier.

It projects each upcoming game's total and spread from the model, compares to
the market, and writes data/teams.json with the edge and the lean — plus the
sample size, because 2-game team ratings lie exactly like 2-game player lines.

Odds API credits are limited (free = 500/mo). One call per run, 3 markets ×
1 region = ~3 credits; run it every 6h and you're well under budget.

Env: BDL_KEY (BALLDONTLIE), ODDS_KEY (The Odds API). SEASON optional (2026).
Never fatal — if either source errors, it records the error and writes what it has.
"""
import json
import os
import statistics
import datetime
import pathlib
import urllib.request
import urllib.parse

BDL_GAMES = "https://api.balldontlie.io/v1/games"
ODDS_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
BDL_KEY = os.environ.get("BDL_KEY", "").strip()
ODDS_KEY = os.environ.get("ODDS_KEY", "").strip()
SEASON = int(os.environ.get("SEASON") or 2026)
HCA = 2.5  # home-court advantage, points


def bdl(url, params=None, cursor=None):
    p = dict(params or {})
    if cursor:
        p["cursor"] = cursor
    if p:
        url += "?" + urllib.parse.urlencode(p, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": BDL_KEY})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)


def fetch_games(season):
    out, cursor, pages = [], None, 0
    while True:
        d = bdl(BDL_GAMES, {"seasons[]": [season], "per_page": 100}, cursor)
        out += d.get("data", [])
        cursor = (d.get("meta", {}) or {}).get("next_cursor")
        pages += 1
        if not cursor or pages >= 40:
            break
    return out


def build_model(games):
    """team abbr -> {gp, pf, pa, ppg, oppg}; also a nickname->abbr map."""
    t = {}
    nick = {}

    def rec(ab):
        return t.setdefault(ab, {"gp": 0, "pf": 0, "pa": 0})

    for g in games:
        ht = g.get("home_team") or {}
        at = g.get("visitor_team") or {}
        hab, aab = ht.get("abbreviation"), at.get("abbreviation")
        if ht.get("full_name") and hab:
            nick[ht["full_name"].split()[-1].lower()] = hab
        if at.get("full_name") and aab:
            nick[at["full_name"].split()[-1].lower()] = aab
        if "Final" not in str(g.get("status", "")):
            continue
        hs, as_ = g.get("home_team_score"), g.get("visitor_team_score")
        if hs is None or as_ is None or not hab or not aab:
            continue
        rh, ra = rec(hab), rec(aab)
        rh["gp"] += 1; rh["pf"] += hs; rh["pa"] += as_
        ra["gp"] += 1; ra["pf"] += as_; ra["pa"] += hs
    for r in t.values():
        g = r["gp"] or 1
        r["ppg"] = round(r["pf"] / g, 1)
        r["oppg"] = round(r["pa"] / g, 1)
    return t, nick


def league_ppg(model):
    tg = sum(r["gp"] for r in model.values())
    tp = sum(r["pf"] for r in model.values())
    return (tp / tg) if tg else 113.0


def fetch_odds():
    if not ODDS_KEY:
        return [], "ODDS_KEY secret is not set"
    url = ODDS_URL + "?" + urllib.parse.urlencode({
        "apiKey": ODDS_KEY, "regions": "us",
        "markets": "h2h,spreads,totals", "oddsFormat": "american"})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SampleSize/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r), None
    except Exception as e:  # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"


def consensus(event):
    home, away = event.get("home_team"), event.get("away_team")
    totals, sp_home, ml_home, ml_away = [], [], [], []
    for bk in event.get("bookmakers", []):
        for mk in bk.get("markets", []):
            key = mk.get("key")
            for oc in mk.get("outcomes", []):
                pt, name, price = oc.get("point"), oc.get("name"), oc.get("price")
                if key == "totals" and name == "Over" and pt is not None:
                    totals.append(pt)
                elif key == "spreads" and name == home and pt is not None:
                    sp_home.append(pt)
                elif key == "h2h" and price is not None:
                    if name == home:
                        ml_home.append(price)
                    elif name == away:
                        ml_away.append(price)
    med = lambda xs: round(statistics.median(xs), 1) if xs else None  # noqa: E731
    medi = lambda xs: int(statistics.median(xs)) if xs else None       # noqa: E731
    return {"home": home, "away": away,
            "mktTotal": med(totals), "mktSpreadHome": med(sp_home),
            "mlHome": medi(ml_home), "mlAway": medi(ml_away)}


def project(home_ab, away_ab, model, lg):
    H, A = model.get(home_ab), model.get(away_ab)
    if not H or not A or H["gp"] == 0 or A["gp"] == 0:
        return None
    expH = H["ppg"] * (A["oppg"] / lg)
    expA = A["ppg"] * (H["oppg"] / lg)
    my_total = round(expH + expA, 1)
    home_margin = round((expH - expA) + HCA, 1)   # + = home favored
    return {"myTotal": my_total, "myHomeMargin": home_margin,
            "mySpreadHome": round(-home_margin, 1),
            "gpH": H["gp"], "gpA": A["gp"]}


def read_game(ev, model, lg, nick):
    c = consensus(ev)
    hab = nick.get((c["home"] or "").split()[-1].lower())
    aab = nick.get((c["away"] or "").split()[-1].lower())
    if not hab or not aab:
        return None
    pr = project(hab, aab, model, lg)
    out = {"ha": aab, "hh": hab, "away": c["away"], "home": c["home"],
           "mktTotal": c["mktTotal"], "mktSpreadHome": c["mktSpreadHome"],
           "mlHome": c["mlHome"], "mlAway": c["mlAway"],
           "myTotal": None, "mySpreadHome": None,
           "totalLean": None, "totalEdge": None,
           "spreadLean": None, "spreadEdge": None, "gpH": None, "gpA": None}
    if pr:
        out.update({"myTotal": pr["myTotal"], "mySpreadHome": pr["mySpreadHome"],
                    "gpH": pr["gpH"], "gpA": pr["gpA"]})
        if c["mktTotal"] is not None:
            e = round(pr["myTotal"] - c["mktTotal"], 1)
            out["totalEdge"] = e
            out["totalLean"] = "OVER" if e > 0 else "UNDER"
        if c["mktSpreadHome"] is not None:
            mkt_home_margin = -c["mktSpreadHome"]
            e = round(pr["myHomeMargin"] - mkt_home_margin, 1)
            out["spreadEdge"] = e
            out["spreadLean"] = hab if e > 0 else aab
    return out


def main():
    err = odds_err = None
    model, nick, lg = {}, {}, 113.0
    games_out = []
    try:
        if not BDL_KEY:
            raise RuntimeError("BDL_KEY secret is not set")
        games = fetch_games(SEASON)
        model, nick = build_model(games)
        lg = round(league_ppg(model), 1)
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"

    events, odds_err = fetch_odds()
    for ev in events:
        r = read_game(ev, model, lg, nick)
        if r:
            games_out.append(r)

    out = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "season": SEASON, "leaguePPG": lg,
        "error": err, "oddsError": odds_err,
        "gameCount": len(games_out),
        "model": model, "games": games_out,
    }
    pathlib.Path("data").mkdir(exist_ok=True)
    pathlib.Path("data/teams.json").write_text(json.dumps(out, indent=2))
    print(f"wrote {len(games_out)} games, {len(model)} teams, leaguePPG {lg}"
          + (f" (error {err})" if err else "") + (f" (odds {odds_err})" if odds_err else ""))


if __name__ == "__main__":
    main()
