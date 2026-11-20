#!/usr/bin/env python3
"""Refresh today's news sections from public RSS feeds.

This script does the mainstream-news and sports half of a refresh run.
It does not call X; use the X connector and scripts/fetch_x.py for On X.

Feeds listed here were checked with curl from this box on 2026-11-19.
Only feeds that returned a usable RSS or Atom document are included.
ESPN's public RSS (https://www.espn.com/espn/rss/news) returned HTTP 202
with an empty body. The official FOX Sports RSS/API endpoints returned
404 or an invalid-partner-key error. Those are omitted. FOX News Sports,
CBS Sports, Yahoo Sports, BBC Sport, ABC Sports, NPR Sports, and CBS
News Sports are used instead.

Usage:
  python3 scripts/refresh_feeds.py
  python3 scripts/refresh_feeds.py --date 2026-11-19
  python3 scripts/refresh_feeds.py --dry-run

The edition date rolls over at midnight America/New_York. Stories are
kept only when their article URL can be fetched successfully. On X posts
are left untouched. After writing the day file, this script runs
add_day.py so data/index.json stays in sync.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ET_ZONE = ZoneInfo("America/New_York")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
MAX_PER_SECTION = 5
MIN_FRESH = 3
# Editor: at most this many stories from one source per section, unless
# the section would otherwise be short.
MAX_PER_SOURCE = 2

# section -> list of feeds. "prefer" is an optional URL-path substring
# list; if set, items whose URL matches are preferred for that section.
FEEDS: list[dict] = [
    # News
    {"url": "https://feeds.npr.org/1001/rss.xml", "source": "NPR", "section": "news"},
    {"url": "https://www.cbsnews.com/latest/rss/main", "source": "CBS News", "section": "news"},
    {"url": "https://abcnews.go.com/abcnews/topstories", "source": "ABC News", "section": "news"},
    {"url": "https://feeds.bbci.co.uk/news/rss.xml", "source": "BBC News", "section": "news"},
    # Politics
    {"url": "https://feeds.npr.org/1014/rss.xml", "source": "NPR", "section": "politics"},
    {"url": "https://www.cbsnews.com/latest/rss/politics", "source": "CBS News", "section": "politics"},
    {"url": "https://abcnews.go.com/abcnews/politicsheadlines", "source": "ABC News", "section": "politics"},
    {"url": "https://rss.politico.com/politics-news.xml", "source": "Politico", "section": "politics"},
    # Economy
    {"url": "https://feeds.npr.org/1006/rss.xml", "source": "NPR", "section": "economy"},
    {"url": "https://www.cbsnews.com/latest/rss/moneywatch", "source": "CBS News", "section": "economy"},
    {"url": "https://abcnews.go.com/abcnews/businessheadlines", "source": "ABC News", "section": "economy"},
    {"url": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "source": "CNBC", "section": "economy"},
    # Sports (ESPN and official FOX Sports/FS1 RSS did not work from this box)
    {"url": "https://feeds.foxnews.com/foxnews/sports", "source": "FOX News Sports", "section": "sports"},
    {"url": "https://www.cbssports.com/rss/headlines/", "source": "CBS Sports", "section": "sports"},
    {"url": "https://sports.yahoo.com/rss/", "source": "Yahoo Sports", "section": "sports"},
    {"url": "https://abcnews.go.com/abcnews/sportsheadlines", "source": "ABC News", "section": "sports"},
    {"url": "https://feeds.npr.org/1008/rss.xml", "source": "NPR", "section": "sports"},
    {"url": "https://www.cbsnews.com/latest/rss/sports", "source": "CBS News", "section": "sports"},
    {"url": "https://feeds.bbci.co.uk/sport/rss.xml", "source": "BBC Sport", "section": "sports"},
    # Movies
    {"url": "https://feeds.npr.org/1045/rss.xml", "source": "NPR", "section": "movies"},
    {
        "url": "https://variety.com/feed/",
        "source": "Variety",
        "section": "movies",
        "prefer": ["/film/", "/movies/"],
    },
    {
        "url": "https://www.hollywoodreporter.com/feed/",
        "source": "The Hollywood Reporter",
        "section": "movies",
        "prefer": ["/movies/", "/film/"],
    },
    # TV
    {
        "url": "https://deadline.com/feed/",
        "source": "Deadline",
        "section": "tv",
        "prefer": ["/tv/", "/television/"],
    },
    {
        "url": "https://variety.com/feed/",
        "source": "Variety",
        "section": "tv",
        "prefer": ["/tv/"],
    },
    {
        "url": "https://abcnews.go.com/abcnews/entertainmentheadlines",
        "source": "ABC News",
        "section": "tv",
        "prefer": ["/Entertainment/", "/entertainment", "/tv", "/Television"],
    },
    # Media
    {
        "url": "https://deadline.com/feed/",
        "source": "Deadline",
        "section": "media",
        "prefer": ["/media/", "/business/", "/awards/"],
    },
    {
        "url": "https://variety.com/feed/",
        "source": "Variety",
        "section": "media",
        "prefer": ["/biz/", "/digital/", "/awards/", "/global/"],
    },
    {"url": "https://www.cbsnews.com/latest/rss/entertainment", "source": "CBS News", "section": "media"},
    # Music
    {"url": "https://feeds.npr.org/1039/rss.xml", "source": "NPR", "section": "music"},
    {"url": "https://www.billboard.com/feed/", "source": "Billboard", "section": "music"},
    {"url": "https://pitchfork.com/feed/feed-news/rss", "source": "Pitchfork", "section": "music"},
    {"url": "https://www.rollingstone.com/music/music-news/feed/", "source": "Rolling Stone", "section": "music"},
    # AI / tech (filtered to AI-related items in collect_section)
    {"url": "https://techcrunch.com/feed/", "source": "TechCrunch", "section": "ai", "ai_only": True},
    {"url": "https://www.theverge.com/rss/index.xml", "source": "The Verge", "section": "ai", "ai_only": True},
    {"url": "https://www.wired.com/feed/rss", "source": "Wired", "section": "ai", "ai_only": True},
    {"url": "https://www.cbsnews.com/latest/rss/technology", "source": "CBS News", "section": "ai", "ai_only": True},
    {"url": "https://abcnews.go.com/abcnews/technologyheadlines", "source": "ABC News", "section": "ai", "ai_only": True},
    {"url": "https://feeds.npr.org/1019/rss.xml", "source": "NPR", "section": "ai", "ai_only": True},
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
SECTION_ORDER = list(SECTION_TITLES)
STORY_SECTIONS = [s for s in SECTION_ORDER if s != "x"]

AI_RE = re.compile(
    r"\b(ai|a\.i\.|artificial intelligence|machine learning|openai|anthropic|"
    r"chatgpt|claude|gemini|deepseek|llm|large language|xai|gpt-?4|gpt-?5|"
    r"copilot|midjourney|stable diffusion|neural net|generative)\b",
    re.I,
)
# Editor: Sports stays national; drop Philadelphia team-specific stories.
PHILLY_RE = re.compile(r"\b(philadelphia|philly|eagles|phillies|76ers|sixers|flyers)\b", re.I)
# Editor: shopping/deal roundups are not news.
JUNK_URL_RE = re.compile(r"/product-recommendations/|/shopping/|/gift-guide", re.I)
JUNK_HEAD_RE = re.compile(r"black friday|cyber monday|gift guide|prime day|promo code|bonus code", re.I)
# Editor: Sports stays national; drop high-school / local prep roundups.
LOCAL_SPORTS_RE = re.compile(r"\b(high school|state playoffs?|prep (football|sports|basketball))\b", re.I)
TAG_RE = re.compile(r"<[^>]+>", re.S)
WS_RE = re.compile(r"\s+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def fail(message: str, code: int = 1) -> None:
    print(f"refresh_feeds.py: {message}", file=sys.stderr)
    raise SystemExit(code)


def today_et() -> str:
    return datetime.now(ET_ZONE).date().isoformat()


def now_et_iso() -> str:
    return datetime.now(ET_ZONE).isoformat(timespec="seconds")


def fetch_bytes(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                payload = res.read()
            if payload[:2] == b"\x1f\x8b":
                # Some feeds (e.g. FOX News) send gzip even when not asked.
                payload = gzip.decompress(payload)
            if payload:
                return payload
            last_error = ValueError("empty response")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        time.sleep(0.8 * (attempt + 1))
    raise last_error or ValueError("empty response")


def local_tag(tag: str) -> str:
    return tag.split("}", 1)[-1] if tag.startswith("{") else tag


def child(el: ET.Element, name: str) -> ET.Element | None:
    for c in list(el):
        if local_tag(c.tag) == name:
            return c
    return None


def child_text(el: ET.Element, name: str) -> str:
    c = child(el, name)
    if c is None or c.text is None:
        return ""
    return c.text.strip()


def item_link(el: ET.Element) -> str:
    # RSS <link>text</link> or Atom <link href>
    for c in list(el):
        if local_tag(c.tag) != "link":
            continue
        href = (c.get("href") or "").strip()
        rel = (c.get("rel") or "alternate").strip()
        text = (c.text or "").strip()
        if href and rel in ("alternate", ""):
            return href
        if href and not rel:
            return href
        if text.startswith("http"):
            return text
    # Some Atom entries use link rel=alternate after other links
    for c in list(el):
        if local_tag(c.tag) == "link" and (c.get("href") or "").startswith("http"):
            return c.get("href", "").strip()
    guid = child(el, "guid")
    if guid is not None and (guid.get("isPermaLink") or "true").lower() == "true":
        if guid.text and guid.text.strip().startswith("http"):
            return guid.text.strip()
    return ""


def parse_when(el: ET.Element) -> str:
    for name in ("pubDate", "published", "updated", "date"):
        raw = child_text(el, name)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(ET_ZONE).isoformat(timespec="seconds")
    return ""


def strip_html(raw: str) -> str:
    text = html.unescape(TAG_RE.sub(" ", raw or ""))
    text = WS_RE.sub(" ", text).strip()
    return text


def summarize(raw: str, headline: str = "") -> str:
    text = strip_html(raw)
    if not text:
        return ""
    # Drop a leading headline echo
    if headline and text.lower().startswith(headline.lower()):
        text = text[len(headline) :].lstrip(" -–—:|")
    parts = [p.strip() for p in SENTENCE_RE.split(text) if p.strip()]
    if not parts:
        return text[:320]
    summary = " ".join(parts[:2])
    if len(summary) > 360:
        summary = summary[:357].rsplit(" ", 1)[0] + "…"
    return summary


def parse_feed(payload: bytes) -> list[dict]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"invalid XML: {exc}") from exc
    items: list[ET.Element] = []
    for el in root.iter():
        if local_tag(el.tag) in ("item", "entry"):
            items.append(el)
    out = []
    for el in items:
        title = strip_html(child_text(el, "title"))
        link = item_link(el)
        # Atom may put summary/content in those tags
        desc = child_text(el, "description") or child_text(el, "summary") or child_text(el, "content")
        encoded = None
        for c in list(el):
            if local_tag(c.tag) in ("encoded", "content") and (c.text or "").strip():
                encoded = c.text
                break
        body = encoded or desc
        if not title or not link.startswith("http"):
            continue
        out.append(
            {
                "headline": title,
                "summary": summarize(body, title),
                "url": link.split("?", 1)[0] if "utm_" in link else link,
                "published": parse_when(el),
            }
        )
    return out


def url_matches(url: str, prefer: list[str] | None) -> bool:
    if not prefer:
        return True
    path = urllib.parse.urlparse(url).path.lower()
    return any(token.lower() in path or token.lower() in url.lower() for token in prefer)


def looks_like_ai(story: dict) -> bool:
    blob = f"{story.get('headline') or ''} {story.get('summary') or ''} {story.get('url') or ''}"
    return bool(AI_RE.search(blob))


def is_junk(story: dict) -> bool:
    return bool(JUNK_URL_RE.search(story.get("url") or "") or JUNK_HEAD_RE.search(story.get("headline") or ""))


def is_philly(story: dict) -> bool:
    """Sports-only guard: Philadelphia team stories or local prep roundups."""
    blob = f"{story.get('headline') or ''} {story.get('summary') or ''} {story.get('url') or ''}"
    return bool(PHILLY_RE.search(blob) or LOCAL_SPORTS_RE.search(blob))


def verify_url(url: str, timeout: int = 12) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    target = url
    for _ in range(6):
        p = urllib.parse.urlparse(target)
        if p.scheme not in ("http", "https") or not p.netloc:
            return False
        path = p.path or "/"
        if p.query:
            path += "?" + p.query
        try:
            import http.client

            cls = http.client.HTTPSConnection if p.scheme == "https" else http.client.HTTPConnection
            conn = cls(p.netloc, timeout=timeout)
            conn.request(
                "GET",
                path,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Connection": "close",
                },
            )
            res = conn.getresponse()
            status = res.status
            location = res.getheader("Location")
            res.read(256)
            conn.close()
        except (OSError, http.client.HTTPException, ValueError):
            return False
        if status in (301, 302, 303, 307, 308) and location:
            target = urllib.parse.urljoin(target, location)
            continue
        return 200 <= status < 400
    return False


def load_day(path: Path) -> dict:
    if not path.is_file():
        return {"date": path.stem, "sections": []}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        fail(f"{path} is not a JSON object")
    return data


def existing_stories(day: dict, section_id: str) -> list[dict]:
    for sec in day.get("sections") or []:
        if sec.get("id") == section_id:
            return list(sec.get("stories") or [])
    return []


def existing_posts(day: dict) -> list[dict]:
    for sec in day.get("sections") or []:
        if sec.get("id") == "x":
            return list(sec.get("posts") or [])
    return []


def story_key(story: dict) -> str:
    url = (story.get("url") or "").strip().rstrip("/").lower()
    if url:
        return "u:" + url
    return "h:" + re.sub(r"\W+", " ", (story.get("headline") or "").lower()).strip()


def collect_candidates(feed: dict, items: list[dict]) -> list[dict]:
    prefer = feed.get("prefer")
    ai_only = bool(feed.get("ai_only"))
    chosen = []
    for item in items:
        story = {
            "headline": item["headline"],
            "summary": item["summary"],
            "source": feed["source"],
            "url": item["url"],
            "published": item.get("published") or "",
        }
        if prefer and not url_matches(story["url"], prefer):
            continue
        if ai_only and not looks_like_ai(story):
            continue
        if is_junk(story) or (feed["section"] == "sports" and is_philly(story)):
            continue
        chosen.append(story)
    if prefer and len(chosen) < 2:
        # If path filters were too strict, fall back to unfiltered items
        # except for AI, which must stay on-topic.
        if not ai_only:
            for item in items:
                story = {
                    "headline": item["headline"],
                    "summary": item["summary"],
                    "source": feed["source"],
                    "url": item["url"],
                    "published": item.get("published") or "",
                }
                if is_junk(story) or (feed["section"] == "sports" and is_philly(story)):
                    continue
                if story_key(story) not in {story_key(s) for s in chosen}:
                    chosen.append(story)
    return chosen


def pick_section(
    candidates: list[dict],
    previous: list[dict],
    verified: set[str],
    failed: set[str],
    used: set[str] | None = None,
    section_id: str = "",
) -> list[dict]:
    ranked = sorted(candidates, key=lambda s: s.get("published") or "", reverse=True)
    used = used if used is not None else set()
    seen: set[str] = set()
    picked: list[dict] = []
    per_source: dict[str, int] = {}
    cap = [MAX_PER_SOURCE]

    def consider(story: dict, already_verified: bool) -> None:
        if len(picked) >= MAX_PER_SECTION:
            return
        key = story_key(story)
        if not key or key in seen or key in used:
            return
        if per_source.get(story.get("source") or "", 0) >= cap[0]:
            return
        if is_junk(story) or (section_id == "sports" and is_philly(story)):
            return
        url = (story.get("url") or "").strip()
        if url:
            if url in failed:
                return
            if not already_verified and url not in verified:
                return
        clean = {
            "headline": story["headline"],
            "summary": story.get("summary") or "",
            "source": story.get("source") or "",
            "url": url,
        }
        if not clean["headline"]:
            return
        seen.add(key)
        per_source[clean["source"]] = per_source.get(clean["source"], 0) + 1
        picked.append(clean)

    for story in ranked:
        url = (story.get("url") or "").strip()
        if url and url in verified:
            consider(story, True)
    if len(picked) < MAX_PER_SECTION:
        # Not enough source variety: lift the per-source cap and refill.
        cap[0] = MAX_PER_SECTION
        for story in ranked:
            url = (story.get("url") or "").strip()
            if url and url in verified:
                consider(story, True)
    # If we don't have enough, fill from previous verified/unverified-ok previous stories
    if len(picked) < MIN_FRESH:
        for story in previous:
            consider(story, True)
    picked = picked[:MAX_PER_SECTION]
    used.update(story_key(s) for s in picked)
    return picked


def build_sections(day: dict, by_section: dict[str, list[dict]], verified: set[str], failed: set[str]) -> list[dict]:
    sections = []
    posts = existing_posts(day)
    used: set[str] = set()  # Editor: no story appears in two sections
    for sec_id in SECTION_ORDER:
        if sec_id == "x":
            if posts:
                sections.append({"id": "x", "title": "On X", "stories": [], "posts": posts})
            continue
        previous = existing_stories(day, sec_id)
        stories = pick_section(by_section.get(sec_id) or [], previous, verified, failed, used, sec_id)
        if not stories:
            continue
        sections.append({"id": sec_id, "title": SECTION_TITLES[sec_id], "stories": stories})
    return sections


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Refresh The Schwartz Spot news sections from RSS.")
    parser.add_argument("--date", help="Edition date YYYY-MM-DD (default: today in Eastern time)")
    parser.add_argument("--dry-run", action="store_true", help="Print planned stories without writing files")
    parser.add_argument("--skip-verify", action="store_true", help="Do not HEAD/GET article URLs (not for publishing)")
    args = parser.parse_args(argv)

    date = args.date or today_et()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        fail("date must be YYYY-MM-DD")

    dest = DATA / f"{date}.json"
    day = load_day(dest)
    day["date"] = date

    # Fetch each unique feed URL once
    unique_urls = []
    seen_urls = set()
    for feed in FEEDS:
        if feed["url"] not in seen_urls:
            unique_urls.append(feed["url"])
            seen_urls.add(feed["url"])

    parsed: dict[str, list[dict]] = {}
    for url in unique_urls:
        try:
            payload = fetch_bytes(url)
            items = parse_feed(payload)
            parsed[url] = items
            print(f"  feed ok  {len(items):3}  {url}")
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            parsed[url] = []
            print(f"  feed FAIL {url}  ({exc})", file=sys.stderr)

    by_section: dict[str, list[dict]] = {s: [] for s in STORY_SECTIONS}
    for feed in FEEDS:
        items = parsed.get(feed["url"]) or []
        for story in collect_candidates(feed, items):
            by_section[feed["section"]].append(story)

    # Verify article links for the first N unique URLs per section
    to_check: list[str] = []
    seen_check = set()
    if not args.skip_verify:
        for sec_id in STORY_SECTIONS:
            # Editor: check the newest few per source (not just the newest 12
            # overall) so one busy feed cannot crowd out the others.
            per_src: dict[str, int] = {}
            ranked = sorted(by_section[sec_id], key=lambda s: s.get("published") or "", reverse=True)
            for story in ranked:
                url = story.get("url") or ""
                src = story.get("source") or ""
                if not url or url in seen_check or per_src.get(src, 0) >= 4:
                    continue
                seen_check.add(url)
                to_check.append(url)
                per_src[src] = per_src.get(src, 0) + 1

    verified: set[str] = set()
    failed: set[str] = set()
    if args.skip_verify:
        verified = {s.get("url") or "" for stories in by_section.values() for s in stories if s.get("url")}
        verified.discard("")
    else:
        print(f"Verifying {len(to_check)} article URLs…")
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(verify_url, url): url for url in to_check}
            for fut in as_completed(futs):
                url = futs[fut]
                ok = False
                try:
                    ok = fut.result()
                except Exception:
                    ok = False
                if ok:
                    verified.add(url)
                else:
                    failed.add(url)
        print(f"  verified {len(verified)}, failed {len(failed)}")

    sections = build_sections(day, by_section, verified, failed)
    if not sections:
        fail("no sections could be built; refusing to write an empty edition")

    out = {
        "date": date,
        "updatedAt": now_et_iso(),
        "sections": sections,
    }

    counts = []
    for sec in sections:
        n_stories = len(sec.get("stories") or [])
        n_posts = len(sec.get("posts") or [])
        if n_posts:
            counts.append(f"{sec['id']}={n_posts} posts")
        else:
            counts.append(f"{sec['id']}={n_stories}")
        if not args.dry_run:
            continue
        if sec.get("stories"):
            print(f"\n[{sec['title']}]")
            for story in sec["stories"]:
                print(f"  - {story['headline']} ({story['source']})")
                print(f"    {story['url']}")

    print("Counts: " + ", ".join(counts))
    print(f"updatedAt {out['updatedAt']}")

    if args.dry_run:
        print("Dry run: no files written")
        return

    DATA.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    sys.path.insert(0, str(ROOT / "scripts"))
    import add_day as add_day_mod

    add_day_mod.main([str(dest)])
    print(f"Wrote {dest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
