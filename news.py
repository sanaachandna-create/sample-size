#!/usr/bin/env python3
"""SAMPLE SIZE - news worker.

Pulls NBA headlines from Google News RSS, writes data/news.json,
and sends new breaking-news alerts to Discord.

Environment variables:
  DISCORD_WEBHOOK
  FAV_TEAM
  WATCHLIST
  TEST_PUSH
"""

import datetime
import html
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


RSS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; SampleSizeBot/1.0; "
        "+https://github.com/sanaachandna-create/sample-size)"
    )
}

DISCORD = (os.environ.get("DISCORD_WEBHOOK") or "").strip()

BREAK_KW = [
    "traded",
    "trade",
    "acquire",
    "acquires",
    "acquired",
    "agrees",
    "agreed",
    "signs",
    "signing",
    "re-sign",
    "waived",
    "waive",
    "released",
    "buyout",
    "injury",
    "injured",
    "surgery",
    "acl",
    "torn",
    "ruptured",
    "sidelined",
    "ruled out",
    "out for",
    "suspended",
    "suspension",
    "doubtful",
    "done for",
]


def fetch(query):
    url = RSS.format(q=urllib.parse.quote(query))
    req = urllib.request.Request(url, headers=HEADERS)

    with urllib.request.urlopen(req, timeout=25) as response:
        return response.read()


def to_epoch(pubdate):
    formats = (
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S GMT",
    )

    for fmt in formats:
        try:
            parsed = datetime.datetime.strptime(pubdate, fmt)
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            continue

    return 0


def parse(xml_bytes, tag):
    items = []

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as error:
        print(f"RSS parse failed for {tag}: {error}")
        return items

    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pubdate = (item.findtext("pubDate") or "").strip()

        source_element = item.find("source")
        source = ""

        if source_element is not None:
            source = (source_element.text or "").strip()

        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)

        title = html.unescape(title)

        if title and link:
            items.append(
                {
                    "title": title,
                    "link": link,
                    "source": source,
                    "ts": to_epoch(pubdate),
                    "tag": tag,
                }
            )

    return items


def collect(query, tag, cap):
    try:
        return parse(fetch(query), tag)[:cap]
    except Exception as error:
        print(
            f"News collection failed for {query!r}: "
            f"{type(error).__name__}: {error}"
        )
        return []


def key_of(title):
    return re.sub(r"\W+", "", title.lower())[:60]


def is_break(title):
    title_lower = title.lower()
    return any(keyword in title_lower for keyword in BREAK_KW)


def discord_push(item):
    if not DISCORD:
        print("Discord push failed: DISCORD_WEBHOOK is empty")
        return False

    tag = item.get("tag", "league")

    color = {
        "team": 15744599,
        "player": 3907575,
    }.get(tag, 10461087)

    body = {
        "username": "SAMPLE SIZE — WIRE",
        "embeds": [
            {
                "title": item["title"][:240],
                "url": item["link"],
                "color": color,
                "footer": {
                    "text": (
                        (item.get("source") or "NBA")
                        + " · "
                        + tag.upper()
                    )
                },
            }
        ],
    }

    try:
        request = urllib.request.Request(
            DISCORD,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": (
                    "DiscordBot "
                    "(https://github.com/"
                    "sanaachandna-create/sample-size, 1.0)"
                ),
            },
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=15) as response:
            print(f"Discord response: HTTP {response.status}")
            return response.status in (200, 204)

    except urllib.error.HTTPError as error:
        error_body = error.read().decode(
            "utf-8",
            errors="replace",
        )[:500]

        print(
            f"Discord push failed: "
            f"HTTP {error.code} — {error_body}"
        )
        return False

    except urllib.error.URLError as error:
        print(
            f"Discord push failed: "
            f"connection error — {error.reason}"
        )
        return False

    except Exception as error:
        print(
            f"Discord push failed: "
            f"{type(error).__name__}: {error}"
        )
        return False


def load_config():
    try:
        config_path = pathlib.Path("data/config.json")
        config = json.loads(config_path.read_text())

        team = (config.get("team", "") or "").strip()

        watchlist = [
            player.strip()
            for player in (config.get("watchlist") or [])
            if player.strip()
        ]

        return team, watchlist

    except Exception:
        return "", []


def send_test_push():
    if os.environ.get("TEST_PUSH") != "1":
        return

    print(f"Discord webhook configured: {bool(DISCORD)}")

    sent = discord_push(
        {
            "title": "TEST — breaking-news pushes are live",
            "link": (
                "https://sanaachandna-create.github.io/"
                "sample-size/"
            ),
            "source": "SAMPLE SIZE",
            "tag": "team",
        }
    )

    if sent:
        print("test push: sent ✓")
    else:
        raise SystemExit(
            "test push failed — see the Discord error above"
        )


def main():
    send_test_push()

    config_team, config_watchlist = load_config()

    favorite_team = (
        config_team
        or (os.environ.get("FAV_TEAM") or "").strip()
    )

    environment_watchlist = [
        player.strip()
        for player in (
            os.environ.get("WATCHLIST") or ""
        ).split(",")
        if player.strip()
    ]

    watchlist = config_watchlist or environment_watchlist

    try:
        previous = json.loads(
            pathlib.Path("data/news.json").read_text()
        )
    except Exception:
        previous = None

    pushed = (previous or {}).get("pushed", [])
    pushed_set = set(pushed)
    first_run = previous is None

    items = []
    pushes = 0
    error_message = None

    try:
        items += collect(
            "NBA basketball",
            "league",
            12,
        )

        items += collect(
            "NBA trade",
            "league",
            8,
        )

        items += collect(
            "NBA injury OR injured",
            "league",
            8,
        )

        if favorite_team:
            items += collect(
                f'"{favorite_team}" NBA',
                "team",
                12,
            )

        for player in watchlist[:6]:
            items += collect(
                f'"{player}" NBA',
                "player",
                6,
            )

        ranking = {
            "team": 0,
            "player": 1,
            "league": 2,
        }

        unique_items = {}

        for item in items:
            item_key = key_of(item["title"])

            if (
                item_key not in unique_items
                or ranking[item["tag"]]
                < ranking[unique_items[item_key]["tag"]]
            ):
                unique_items[item_key] = item

        items = sorted(
            unique_items.values(),
            key=lambda item: item["ts"],
            reverse=True,
        )

        breaking_items = [
            item
            for item in items
            if (
                is_break(item["title"])
                and key_of(item["title"]) not in pushed_set
            )
        ]

        breaking_items = sorted(
            breaking_items,
            key=lambda item: item["ts"],
            reverse=True,
        )

        # On the first-ever run, remember existing stories without
        # flooding Discord with old alerts.
        if first_run:
            for item in breaking_items:
                pushed.append(key_of(item["title"]))

        else:
            # Send no more than five alerts during one run.
            for item in breaking_items[:5]:
                if discord_push(item):
                    pushes += 1
                    pushed.append(key_of(item["title"]))
                else:
                    print(
                        "Alert was not marked as pushed, "
                        "so it can retry later."
                    )

        pushed = pushed[-500:]
        items = items[:30]

    except Exception as error:
        error_message = (
            f"{type(error).__name__}: {error}"
        )

        print(f"News processing failed: {error_message}")

    output = {
        "updated": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(timespec="seconds"),
        "team": favorite_team,
        "count": len(items),
        "pushed_this_run": pushes,
        "error": error_message,
        "items": items,
        "pushed": pushed,
    }

    pathlib.Path("data").mkdir(exist_ok=True)

    pathlib.Path("data/news.json").write_text(
        json.dumps(output, indent=2)
    )

    message = (
        f"wrote {len(items)} headlines, "
        f"pushed {pushes}"
    )

    if error_message:
        message += f" (error {error_message})"

    print(message)


if __name__ == "__main__":
    main()
