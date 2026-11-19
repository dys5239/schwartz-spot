#!/usr/bin/env python3
"""Append a morning edition to The Schwartz Spot archive.

Reads a JSON brief, writes data/YYYY-MM-DD.json, and updates data/index.json
so the site can show a new day without a build step.

Usage:
  python3 scripts/add_day.py path/to/brief.json
  python3 scripts/add_day.py --stdin < brief.json

A brief looks like:

{
  "date": "YYYY-MM-DD",
  "sections": [
    {
      "id": "ai",
      "title": "AI",
      "stories": [
        {
          "headline": "Required",
          "summary": "One or two sentences.",
          "source": "The source name",
          "url": "https://example.com/story-or-empty-string"
        }
      ]
    }
  ]
}

Section ids, in site order: ai, news, x, politics, economy, sports,
movies, tv, media, music. Omit a section if it has no stories or posts.
The ``x`` section (On X) holds a ``posts`` array instead of stories;
refresh it with scripts/fetch_x.py after fetching via the X connector.
Sports should carry national headlines, not city- or team-specific
local coverage.
Unknown fields should be empty strings, never guessed.
Existing day files are overwritten so a corrected edition can be re-imported.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INDEX = DATA / "index.json"

SECTION_ORDER = [
    "ai",
    "news",
    "x",
    "politics",
    "economy",
    "sports",
    "movies",
    "tv",
    "media",
    "music",
]
SECTION_TITLES = {
    "ai": "AI",
    "news": "News",
    "x": "On X",
    "politics": "Politics",
    "economy": "Economy",
    "sports": "Sports",
    "movies": "Movies",
    "tv": "TV",
    "media": "Media",
    "music": "Music",
}
STORY_FIELDS = ("headline", "summary", "source", "url")
POST_FIELDS = ("id", "created_at", "author_name", "author_handle", "text", "url")


def fail(message: str, code: int = 1) -> None:
    print(f"add_day.py: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_brief(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON: {exc}")
    if not isinstance(data, dict):
        fail("brief must be a JSON object")
    return data


def clean_text(value, *, field: str, required: bool = False) -> str:
    if value is None:
        text = ""
    elif isinstance(value, (str, int, float)):
        text = str(value).strip()
    else:
        fail(f"{field} must be a string")
    if required and not text:
        fail(f"{field} is required")
    return text


def normalize_stories(items, *, label: str) -> list[dict]:
    if items is None:
        items = []
    if not isinstance(items, list):
        fail(f"{label} must be a list")
    stories = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            fail(f"{label}[{i}] must be an object")
        story = {key: clean_text(item.get(key), field=f"{label}[{i}].{key}") for key in STORY_FIELDS}
        story["headline"] = clean_text(item.get("headline"), field=f"{label}[{i}].headline", required=True)
        stories.append(story)
    return stories


def normalize_posts(items, *, label: str) -> list[dict]:
    if items is None:
        items = []
    if not isinstance(items, list):
        fail(f"{label} must be a list")
    posts = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            fail(f"{label}[{i}] must be an object")
        post = {key: clean_text(item.get(key), field=f"{label}[{i}].{key}") for key in POST_FIELDS}
        post["id"] = clean_text(item.get("id"), field=f"{label}[{i}].id", required=True)
        post["created_at"] = clean_text(item.get("created_at"), field=f"{label}[{i}].created_at", required=True)
        post["author_handle"] = clean_text(item.get("author_handle"), field=f"{label}[{i}].author_handle", required=True)
        post["text"] = "" if item.get("text") is None else str(item.get("text"))
        verified = item.get("verified")
        post["verified"] = True if verified is True else False if verified is False else bool(verified)
        post["verified_type"] = clean_text(item.get("verified_type"), field=f"{label}[{i}].verified_type")
        posts.append(post)
    posts.sort(key=lambda p: p.get("created_at") or "", reverse=True)
    return posts


def normalize_sections(brief: dict) -> list[dict]:
    raw = brief.get("sections")
    by_id: dict[str, dict] = {}

    if raw is None and brief.get("stories"):
        raw = [{"id": "ai", "title": "AI", "stories": brief.get("stories")}]

    if raw is None:
        raw = []

    if isinstance(raw, dict):
        items = []
        for key, value in raw.items():
            if isinstance(value, dict):
                items.append({"id": value.get("id") or key, **value})
            elif isinstance(value, list):
                items.append({"id": key, "stories": value})
            else:
                fail(f"sections.{key} must be an object or a list of stories")
        raw = items

    if not isinstance(raw, list):
        fail("sections must be a list or an object keyed by section id")

    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            fail(f"sections[{i}] must be an object")
        sec_id = clean_text(item.get("id"), field=f"sections[{i}].id", required=True)
        if sec_id not in SECTION_TITLES:
            fail(f"unknown section id '{sec_id}'; use one of: {', '.join(SECTION_ORDER)}")
        title = clean_text(item.get("title"), field=f"sections[{i}].title") or SECTION_TITLES[sec_id]
        stories = normalize_stories(item.get("stories"), label=f"sections[{i}].stories")
        posts = normalize_posts(item.get("posts"), label=f"sections[{i}].posts")
        if not stories and not posts:
            continue
        entry = {"id": sec_id, "title": title, "stories": stories}
        if posts:
            entry["posts"] = posts
        by_id[sec_id] = entry

    return [by_id[sec_id] for sec_id in SECTION_ORDER if sec_id in by_id]


def normalize(brief: dict) -> dict:
    date = clean_text(brief.get("date"), field="date", required=True)
    if not DATE_RE.match(date):
        fail("date must be YYYY-MM-DD")
    if "jobs" in brief and brief.get("jobs"):
        fail("jobs are no longer part of The Schwartz Spot; remove the jobs field")
    sections = normalize_sections(brief)
    if not sections:
        fail("at least one section with stories or posts is required")
    out = {"date": date, "sections": sections}
    updated = clean_text(brief.get("updatedAt"), field="updatedAt")
    if updated:
        out["updatedAt"] = updated
    return out


def load_index() -> list[dict]:
    if not INDEX.exists():
        return []
    payload = json.loads(INDEX.read_text())
    dates = payload if isinstance(payload, list) else payload.get("dates", [])
    out = []
    for item in dates:
        if isinstance(item, str):
            out.append({"date": item, "storyCount": 0})
        elif isinstance(item, dict) and item.get("date"):
            row = {"date": item["date"], "storyCount": int(item.get("storyCount") or 0)}
            if item.get("postCount"):
                row["postCount"] = int(item["postCount"])
            if item.get("updatedAt"):
                row["updatedAt"] = str(item["updatedAt"])
            out.append(row)
    return out


def story_count(day: dict) -> int:
    return sum(len(sec.get("stories") or []) for sec in day.get("sections") or [])


def post_count(day: dict) -> int:
    return sum(len(sec.get("posts") or []) for sec in day.get("sections") or [])


def write_index(entries: list[dict]) -> None:
    entries = [e for e in entries if DATE_RE.match(e["date"])]
    entries.sort(key=lambda e: e["date"], reverse=True)
    payload = {"dates": entries}
    latest = next((e.get("updatedAt") for e in entries if e.get("updatedAt")), None)
    if latest:
        payload["updatedAt"] = latest
    INDEX.write_text(json.dumps(payload, indent=2) + "\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Add or replace one day in The Schwartz Spot archive.")
    parser.add_argument("path", nargs="?", help="Path to a brief JSON file")
    parser.add_argument("--stdin", action="store_true", help="Read the brief from stdin")
    args = parser.parse_args(argv)

    if args.stdin and args.path:
        fail("pass a file path or --stdin, not both")
    if args.stdin:
        raw = sys.stdin.read()
        origin = "stdin"
    elif args.path:
        path = Path(args.path)
        if not path.is_file():
            fail(f"file not found: {path}")
        raw = path.read_text()
        origin = str(path)
    else:
        fail("provide a brief JSON file or --stdin")

    day = normalize(load_brief(raw))
    DATA.mkdir(parents=True, exist_ok=True)
    dest = DATA / f"{day['date']}.json"
    dest.write_text(json.dumps(day, indent=2, ensure_ascii=False) + "\n")

    index = [e for e in load_index() if e["date"] != day["date"]]
    entry = {"date": day["date"], "storyCount": story_count(day)}
    posts = post_count(day)
    if posts:
        entry["postCount"] = posts
    if day.get("updatedAt"):
        entry["updatedAt"] = day["updatedAt"]
    index.append(entry)
    write_index(index)
    print(f"Wrote {dest.relative_to(ROOT)} from {origin} and updated data/index.json")


if __name__ == "__main__":
    main()
