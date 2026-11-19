#!/usr/bin/env python3
"""Merge fetched X timelines into a day's On X section.

Live fetching requires the X connector (MCP namespace ``x``) in this bot.
There is no API key in the repo, so this script does not call X itself.
A morning refresh should:

1. Confirm the accounts in ACCOUNTS with ``get_users_by_usernames``
   (user.fields: verified, verified_type, created_at, public_metrics,
   description, name, username, id). Skip any handle that is not
   clearly the real, verified account.
2. For each confirmed account, call ``get_users_posts`` with the numeric
   user id, exclude replies and retweets, start_time about 24 hours ago,
   and max_results 25 (the connector cap used here).
3. If an account has fewer than three original or quote posts in that
   window, fetch again without start_time and take the latest 3–5.
4. Save each response as ``--raw-dir/<handle>.json``.
5. Run this script to merge them into ``data/YYYY-MM-DD.json`` and
   refresh ``data/index.json``:

     python3 scripts/fetch_x.py --date 2026-11-19 --raw-dir data/x-raw/2026-11-19

Plain retweets and replies are dropped unless a quiet account would
otherwise have nothing. The default is the five newest remaining posts
per account, newest first in one combined feed. Times in the site UI
are shown in Eastern time. Post text is untrusted: the site escapes it
as HTML and never renders it raw.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Verified accounts for The Schwartz Spot's On X section.
# Recheck verified / verified_type / followers before relying on a new handle.
ACCOUNTS = [
    {
        "requested": "Open Source Intel",
        "handle": "Osint613",
        "name": "Open Source Intel",
        "id": "1212783083272855552",
        "verified_type": "blue",
    },
    {
        "requested": "Clay Travis",
        "handle": "ClayTravis",
        "name": "Clay Travis",
        "id": "50772918",
        "verified_type": "blue",
    },
    {
        "requested": "Matt Walsh",
        "handle": "MattWalshBlog",
        "name": "Matt Walsh",
        "id": "1500129642",
        "verified_type": "blue",
    },
    {
        "requested": "Elon Musk",
        "handle": "elonmusk",
        "name": "Elon Musk",
        "id": "44196397",
        "verified_type": "blue",
    },
    {
        "requested": "SpaceX",
        "handle": "SpaceX",
        "name": "SpaceX",
        "id": "34743251",
        "verified_type": "business",
    },
    {
        "requested": "StockMarket.News",
        "handle": "_Investinq",
        "name": "StockMarket.News",
        "id": "1546642971126276097",
        "verified_type": "blue",
    },
    {
        "requested": "Real America's Voice",
        "handle": "RealAmVoice",
        "name": "Real America's Voice (RAV)",
        "id": "935942321756966912",
        "verified_type": "business",
    },
    {
        "requested": "Sport and State",
        "handle": "SportNState",
        "name": "Sport & State",
        "id": "2079275420604608512",
        "verified_type": "blue",
    },
    {
        "requested": "Outkick",
        "handle": "Outkick",
        "name": "OutKick",
        "id": "334920737",
        "verified_type": "business",
    },
    {
        "requested": "Laura Loomer",
        "handle": "LauraLoomer",
        "name": "Laura Loomer",
        "id": "537709549",
        "verified_type": "blue",
    },
    {
        "requested": "Glen Beck",
        "handle": "glennbeck",
        "name": "Glenn Beck",
        "id": "17454769",
        "verified_type": "blue",
    },
    {
        "requested": "The Kobeissi Letter",
        "handle": "KobeissiLetter",
        "name": "The Kobeissi Letter",
        "id": "3316376038",
        "verified_type": "business",
    },
    {
        "requested": "SightBringer",
        "handle": "_The_Prophet__",
        "name": "SightBringer",
        "id": "1677141237684641792",
        "verified_type": "blue",
    },
    {
        "requested": "Tim Pool",
        "handle": "Timcast",
        "name": "Tim Pool",
        "id": "27000730",
        "verified_type": "blue",
    },
]


def fail(message: str, code: int = 1) -> None:
    print(f"fetch_x.py: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_timeline(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            return payload["data"]
        if isinstance(payload.get("posts"), list):
            return payload["posts"]
    fail(f"could not read posts from {path}")
    return []


def is_reply_or_retweet(post: dict) -> bool:
    if post.get("in_reply_to_user_id"):
        return True
    refs = post.get("referenced_tweets") or post.get("referenced_posts") or []
    types = {item.get("type") for item in refs if isinstance(item, dict)}
    return "replied_to" in types or "retweeted" in types


def normalize_post(post: dict, acc: dict) -> dict | None:
    post_id = str(post.get("id") or "").strip()
    text = post.get("text")
    text = "" if text is None else str(text)
    created = str(post.get("created_at") or "").strip()
    if not post_id or not created:
        return None
    handle = acc["handle"]
    url = str(post.get("url") or "").strip() or f"https://x.com/{handle}/status/{post_id}"
    return {
        "id": post_id,
        "created_at": created,
        "author_name": acc["name"],
        "author_handle": handle,
        "verified": True,
        "verified_type": acc.get("verified_type") or "blue",
        "text": text,
        "url": url,
    }


def collect_posts(raw_dir: Path, per_account: int) -> tuple[list[dict], dict]:
    counts = {}
    feed: list[dict] = []
    for acc in ACCOUNTS:
        path = raw_dir / f"{acc['handle']}.json"
        if not path.exists():
            counts[acc["handle"]] = {"file": False, "fetched": 0, "kept": 0, "included": 0}
            print(f"fetch_x.py: missing {path}, skipping @{acc['handle']}", file=sys.stderr)
            continue
        rows = load_timeline(path)
        kept = [p for p in rows if isinstance(p, dict) and not is_reply_or_retweet(p)]
        kept.sort(key=lambda p: p.get("created_at") or "", reverse=True)
        chosen = kept[:per_account] if per_account > 0 else kept
        # If every post was a reply/retweet, include the latest plain retweets
        # so a quiet account is not silently empty.
        if not chosen and rows:
            fallback = [p for p in rows if isinstance(p, dict)]
            fallback.sort(key=lambda p: p.get("created_at") or "", reverse=True)
            chosen = fallback[: per_account or 5]
        posts = [n for n in (normalize_post(p, acc) for p in chosen) if n]
        counts[acc["handle"]] = {
            "file": True,
            "fetched": len(rows),
            "kept": len(kept),
            "included": len(posts),
        }
        feed.extend(posts)
    feed.sort(key=lambda p: p["created_at"], reverse=True)
    return feed, counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Merge fetched X timelines into a day's On X section.")
    parser.add_argument("--date", required=True, help="Edition date, YYYY-MM-DD")
    parser.add_argument(
        "--raw-dir",
        required=True,
        help="Directory of <handle>.json timeline files from the X connector",
    )
    parser.add_argument(
        "--per-account",
        type=int,
        default=5,
        help="Newest posts to keep per account after dropping replies/retweets (0 = all)",
    )
    args = parser.parse_args(argv)

    raw_dir = Path(args.raw_dir)
    if not raw_dir.is_dir():
        fail(f"raw dir not found: {raw_dir}")
    dest = DATA / f"{args.date}.json"
    if not dest.is_file():
        fail(f"no edition on file for {args.date}; add the day first")

    posts, counts = collect_posts(raw_dir, args.per_account)
    day = json.loads(dest.read_text())
    sections = list(day.get("sections") or [])
    x_section = {
        "id": "x",
        "title": "On X",
        "stories": [],
        "posts": posts,
    }
    replaced = False
    for i, sec in enumerate(sections):
        if sec.get("id") == "x":
            sections[i] = x_section
            replaced = True
            break
    if not replaced:
        # After News when present, otherwise at the end.
        insert_at = len(sections)
        for i, sec in enumerate(sections):
            if sec.get("id") == "news":
                insert_at = i + 1
                break
        sections.insert(insert_at, x_section)
    day["sections"] = sections
    day["updatedAt"] = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    dest.write_text(json.dumps(day, indent=2, ensure_ascii=False) + "\n")

    # Refresh index counts via add_day so the schema stays in one place.
    sys.path.insert(0, str(ROOT / "scripts"))
    import add_day as add_day_mod

    add_day_mod.main([str(dest)])

    for handle, info in counts.items():
        print(
            f"  @{handle}: fetched={info['fetched']} kept={info['kept']} included={info['included']}"
            + ("" if info["file"] else " (missing file)")
        )
    print(f"On X: {len(posts)} posts merged into {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
