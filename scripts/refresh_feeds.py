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
# Editor (Nov 21): stories older than this are used only after fresher ones
# (stale ABC/NPR items sat in the slate for every run on Nov 20 and 21).
FRESH_HOURS = 36
# Editor (Nov 23): subject-level diversity. Dave: "too many stories of Jensen
# Huang being out at Nvidia ..he apparently came back today" (9 of 45 slots on
# Nov 23). Stories that share a hot named subject (a name in the headlines of at
# least TOPIC_HOT_MIN distinct candidate stories, e.g. Jensen Huang / Nvidia) are
# capped at TOPIC_MAX_PER_SECTION per section and TOPIC_MAX_PER_EDITION across
# the edition; the freshest one wins and the slot is backfilled.
TOPIC_HOT_MIN = 3
TOPIC_MAX_PER_SECTION = 1
TOPIC_MAX_PER_EDITION = 2
# A subject story this many hours older than the subject's newest story is
# superseded (dropped), as is a departure headline once a return/reversal exists.
SUPERSEDE_HOURS = 24
# Editor (Nov 26): within a section, two stories whose headline + summary lead
# share this many named entities are the same subject even if no single name is
# hot (CBS "Trump claims 'fake economic numbers'..." + ABC "Trump calls the
# pre-election GDP figures 'fake'..." both name GDP / Commerce / Department).
TOPIC_SHARED_ENTITIES = 3

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
    # Editor (Nov 26): NBC News politics replaces Politico, whose article pages
    # return a Cloudflare 403 challenge to this box, so no Politico story ever
    # passed link verification (0 appearances Nov 19-26).
    # Only nbcnews.com/politics/ articles (the feed also carries U.S.-news
    # items and TODAY / Meet the Press video clips).
    {"url": "https://feeds.nbcnews.com/nbcnews/public/politics", "source": "NBC News", "section": "politics", "prefer": ["nbcnews.com/politics/"]},
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
    # Editor (Nov 24): THR's main feed had <2 /movies/ items, so the fallback put
    # a TV interview and a Broadway labor story in Movies; use the movies-category
    # feed (10/10 movie items, all fresh), like TV's /c/tv/feed/.
    {
        "url": "https://www.hollywoodreporter.com/c/movies/feed/",
        "source": "The Hollywood Reporter",
        "section": "movies",
        "prefer": ["/movies/", "/film/"],
    },
    # TV
    # Editor (Nov 20): Deadline's main feed URLs carry no section path, so the
    # old "prefer" never matched and non-TV items (e.g. court news) leaked in.
    # Its TV-category feed is already TV-only.
    {"url": "https://deadline.com/v/tv/feed/", "source": "Deadline", "section": "tv"},
    # Editor (Nov 22): Variety's main feed had <2 /tv/ items, so the fallback
    # put music and film stories (Don Toliver concert, Avengers casting) in TV.
    # Use Variety's TV-category feed.
    {
        "url": "https://variety.com/v/tv/feed/",
        "source": "Variety",
        "section": "tv",
        "prefer": ["/tv/"],
    },
    # Editor (Nov 22): ABC entertainment is general entertainment (its "prefer"
    # paths match everything): stale "Josephine" all day Nov 20-21, then a box-
    # office story and a music wedding in TV on Nov 22. THR's TV feed replaces it.
    {
        "url": "https://www.hollywoodreporter.com/c/tv/feed/",
        "source": "The Hollywood Reporter",
        "section": "tv",
    },
    # Media
    # Editor (Nov 20): Media = the media business. THR's business feed replaces
    # Deadline's unfiltered main feed.
    # Editor (Nov 21): Variety's main feed had no /biz/ items on the weekend, so
    # the "prefer" fallback filled Media with film interviews; use Variety's
    # business-category feed instead. CBS entertainment (book excerpts, Saturday
    # Sessions music videos) is replaced by Deadline's business-category feed.
    {
        "url": "https://www.hollywoodreporter.com/c/business/feed/",
        "source": "The Hollywood Reporter",
        "section": "media",
    },
    {"url": "https://variety.com/v/biz/feed/", "source": "Variety", "section": "media"},
    {"url": "https://deadline.com/v/business/feed/", "source": "Deadline", "section": "media"},
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
# Sports stays national: Philly teams are fine, but skip local Philadelphia-area outlets (Dave, Nov 20, 2026).
LOCAL_OUTLET_RE = re.compile(
    r"(inquirer\.com|phillyvoice\.com|nbcphiladelphia\.com|6abc\.com|fox29\.com|cbsnews\.com/philadelphia|"
    r"phillymag\.com|billypenn\.com|whyy\.org|audacy\.com/kyw|audacy\.com/941wip|crossingbroad\.com|"
    r"phillysportsnetwork\.com|nbcsportsphiladelphia\.com|delcotimes\.com|dailylocal\.com|"
    r"Philadelphia Inquirer|PhillyVoice|NBC10|6abc|FOX 29|KYW|94WIP|Crossing Broad|Philly Sports Network)",
    re.I,
)
# Editor: shopping/deal roundups are not news.
# Editor (Nov 21): Variety "Strictly Business" sponsored tax/charity pieces.
# Editor (Nov 23): sportsbook guides and promo posts live under /betting/ (CBS Sports).
# Editor (Nov 23, night): NPR "Up First" newsletter digests (several topics in one
# headline) held a Politics slot 31 of 33 runs and duplicated the House story.
# Editor (Nov 24): Variety ran a BOTOX(R) ad ("Medication Guide, Boxed Warning")
# in Media for 14 runs; Billboard's evergreen "Music Industry Events Calendar".
JUNK_URL_RE = re.compile(r"/product-recommendations/|/shopping/|/gift-guide|-strictly-\d+/?$|/betting/|/up-first-newsletter|/biz/lifestyle/|events-calendar", re.I)
JUNK_HEAD_RE = re.compile(
    # Editor (Nov 26): only Black Friday / Cyber Monday DEAL posts, not retail news
    # ("Necessities join gifts in Black Friday shopping carts", scam warnings).
    r"(black friday|cyber monday)\b.*\b(deals?|sales?|discounts?|off|savings|doorbusters?|picks)\b|"
    r"\b(deals?|sales?|discounts?|percent off|savings)\b.*\b(black friday|cyber monday)|\bup to \d+% off|coupons?\b|discount codes?|what stores are open|store hours|what's open (and closed )?on|"
    r"gift guide|prime day|promo code|bonus code|"
    r"bonus(es)? (from|at|with) (betting|sportsbook)|"  # Editor (Nov 21)
    r"^\d{1,2}/\d{1,2}(/\d{2,4})?: (CBS|Face the Nation)",  # Editor (Nov 26): full-episode videos ("11/26: CBS Evening News")
    re.I,
)
# Editor (Nov 23): Sports only — sportsbook / betting-app guides and promos
# ("Arkansas Sports Betting 2026: Best Legal AR Sportsbooks & Betting Apps").
# Sports-only so real news about betting laws can still run in News/Politics.
SPORTS_BETTING_RE = re.compile(
    r"sportsbooks?|sports ?betting|betting (apps?|guides?|sites?|promos?|bonus(es)?|offers?|lines?|tips?)|"
    r"legal (online )?(sports )?betting|best bets?|bonus bets?|odds boosts?|\bpromos?\b|parlays?|"
    r"draftkings|fanduel|kalshi|prizepicks|betmgm|caesars sportsbook|fanatics sportsbook|underdog fantasy",
    re.I,
)
# Editor (Nov 22): BBC "The Papers" press review and CBS "Sunday Morning" promo posts.
JUNK_NEWS_RE = re.compile(r"^the papers:|^this week on .?sunday morning", re.I)
# Editor (Nov 22): Sports — AP automated small-college score templates
# ("Lipscomb secures 92-67 win over Milligan", "George Mason defeats South
# Florida 73-62") and betting odds/picks posts. Not a team filter.
SPORTS_TEMPLATE_RE = re.compile(
    r"\b(secures|earns) (an? )?\d+-\d+ (win|victory) (over|against)\b|\bdefeats [^,]+ \d+-\d+$|"
    r"^[^,]+ (scores|puts up|has|posts|totals) \d+, [^,]+ \d+-\d+$|"
    r"^[^,\s]+[’']s? \d+ (help|helps|lead|leads|lift|lifts|power|powers|carry|carries) [^,]+ \d+-\d+$|"
    # Editor (Nov 23): AP small-college "Name scores 28 to help Utah Valley hold off ..." template.
    r"^[^,]+ (scores|puts up|posts|totals|has) \d+ (points )?to (help|lead|lift|power|carry) |"
    # Editor (Nov 25): AP small-college "Name scores 27 in Utah Tech's 124-54 win against
    # Bethesda (CA)" / "Name scores 20 as Cleveland State beats Case Western 76-46"
    # (7 seen Nov 15-25, none with a ranked team). Ranked ("No. 16") games still pass.
    r"^(?!.*\bNo\. \d)[^,]+ (scores|puts up|posts|totals|has) \d+ (points )?(in|as) [^,]+ \d+-\d+|"
    # Editor (Nov 26): AP small-college "Montana State takes down Northwest Indian 110-43"
    # (20 runs on Nov 26) / "Odih scores 15 off the bench, Fordham knocks off Mercy 104-56".
    # Headline must end on the score; ranked games still pass.
    r"^(?!.*\bNo\. \d)(?:[^,]+, )?[^,]+ (takes down|knocks off) [^,]+ \d+-\d+$|"
    r"\bodds\b.*\b(picks?|prediction|best bets?)\b|\b(prediction|picks?)\b.*\bodds\b",
    re.I,
)
# Editor (Nov 25): Billboard's "Duran Duran's Biggest Billboard Hits" list took the
# Music slot while the actual news (guitarist Andy Taylor dies, Pitchfork/Rolling
# Stone) was blocked as the same subject. Such lists rank after fresh news.
MUSIC_EVERGREEN_RE = re.compile(
    r"biggest (billboard )?hits|greatest hits:|best songs|essential (songs|albums)|songs? (of all time|to know)",
    re.I,
)
# Editor (Nov 22): TV keeps TV; film/music URLs and box-office stories belong elsewhere.
TV_OFF_RE = re.compile(r"/(film|music|movies)/|box office", re.I)
# Editor (Nov 24): THR's movie feed falls back to TV items; a 'Dancing With the
# Stars' finalist interview (/tv/tv-features/) ran in Movies. Mirror of TV_OFF_RE.
MOVIES_OFF_RE = re.compile(r"\.com/tv/|/(tv|television)/", re.I)
# Editor (Nov 23, night): Sports stays national. Yahoo Sports syndicates local
# papers and TV stations (Spokesman-Review Gonzaga/Eastern Washington items on
# Nov 20, 22 and 23; CT Insider prep football; WPXI). The original outlet is in
# the item's <source>; drop local newspapers and call-sign TV stations. National
# outlets (AP, USA TODAY, NBC Sports, SB Nation, team "Wire" sites) still pass.
LOCAL_ORIGIN_RE = re.compile(
    r",\s*[A-Z][a-z]+\.?$|^(The )?[KW][A-Z]{2,3}\b|Insider$|Spokesman|Times-Union|Enquirer|Gazette|Courier|"
    r"Tribune|Star-Telegram|Register|Dispatch|Herald|Journal|Free Press|Plain Dealer|Oklahoman|Tennessean|"
    r"Commercial Appeal|Deseret|Patriot-News|PennLive|Morning Call|Sentinel|Daily News|Statesman|Advocate|"
    r"Clarion|Citizen|Chronicle|Post-Gazette|Union-Tribune|Times-Picayune|Democrat",
)
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


# Editor (Nov 26): Yahoo/USA TODAY network summaries can open with a photo
# credit ("IMAGN IMAGES via Reuters Connect Gabe Landeskog didn't ...").
PHOTO_CREDIT_RE = re.compile(r"^(?:IMAGN IMAGES|Imagn Images|USA TODAY Sports|Getty Images)(?: via Reuters Connect)?\s*[-|:]?\s*")


def summarize(raw: str, headline: str = "") -> str:
    text = strip_html(raw)
    if not text:
        return ""
    text = PHOTO_CREDIT_RE.sub("", text)
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
        # Editor (Nov 23, night): syndicating feeds (Yahoo Sports) name the
        # original outlet in <source> / dc:publisher.
        origin = child_text(el, "source") or child_text(el, "publisher")
        out.append(
            {
                "origin": strip_html(origin),
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


# Editor (Nov 23): sports items that leak into News / Politics (ABC's politics
# feed carries AP NFL features under /Politics/wireStory/; BBC News carries BBC
# Sport videos). Matched by URL path and sports vocabulary, not team names.
SPORTS_URL_RE = re.compile(r"/sports?/|//sports?\.|/sport-|cbssports\.com|espn\.com", re.I)
SPORTS_TERMS_RE = re.compile(
    r"\b(NFL|NBA|NHL|MLB|WNBA|NWSL|MLS|NCAA|CFP|quarterbacks?|linebackers?|running backs?|wide receivers?|"
    r"tight ends?|cornerbacks?|touchdowns?|TDs?|field goals?|interceptions?|run game|passing game|"
    r"offensive coordinator|defensive coordinator|head coach|halftime|overtime win|playoffs?|postseason|"
    r"goalkeeper|goalie|striker|midfielder|hat[- ]trick|Premier League|La Liga|Serie A|Bundesliga|"
    r"Champions League|Europa League|World Cup qualif\w*|Grand Slam|PGA Tour|NASCAR|UFC|"
    r"(first|second) (NFL|NBA|career) start|QB|LB|WR|RB|CB|AFC|NFC|"
    r"kickers?|punters?|rushing yards|won \d+ straight|\d+-game (skid|winning streak|losing streak))\b|"
    r"\b[Oo]ffense\b.*\b[Dd]efense\b|\b[Dd]efense\b.*\b[Oo]ffense\b|"
    r"\b(Super Bowl|FIFA|LIV|PGA|snapped an? [\w-]+ (losing|winning) streak|"
    r"\d{1,3}-\d{1,3} (win|victory|loss|defeat))\b|\bon offense\b"
)


def is_sports_item(story: dict) -> bool:
    if SPORTS_URL_RE.search(story.get("url") or ""):
        return True
    blob = f"{story.get('headline') or ''} {story.get('summary') or ''}"
    return bool(SPORTS_TERMS_RE.search(blob))


# Editor (Nov 23): Dave wants Sports to be about actual games. Stories are tiered
# (0 result/recap, 1 preview or standings/playoff stakes, 2 other on-field news,
# 3 off-field or minor sports) and picked in tier order, freshest first.
GAME_RESULT_RE = re.compile(
    r"(?<![\d-])\d{1,3}-\d{1,3}(?![\d-])|\b(beat|beats|defeat(s|ed)?|tops|rout(s|ed)?|edges?|downs|"
    r"rall(y|ies) past|win over|wins over|victory over|falls? to|stuns?|stunned|upsets?|thrash(es)?|"
    r"draws? with|held to|recap|takeaways|instant analysis|final score|"
    r"late winner|winner|comeback win|in overtime|walk-?off|shutout|clinch(es|ed)?)\b",
    re.I,
)
GAME_PREVIEW_RE = re.compile(
    r"\bvs\.?\b|\bv\b|matchup|showdown|preview|how to watch|what to watch|who plays|kickoff|tip-?off|"
    r"face-?off|clash|set for|first-place battle|battle for|rivalry|week \d+|thanksgiving (games?|schedule)|"
    r"monday night football|sunday night football|thursday night football|\bMNF\b|\bSNF\b|\bTNF\b|"
    r"standings|playoffs?|postseason|clinch|\bCFP\b|bowl game|title game|championship|semifinals?|"
    r"quarterfinals?|knockout|first place|division lead|top seed|no\. \d+",
    re.I,
)
OFF_FIELD_RE = re.compile(
    r"contracts?|extensions?|re-sign|\bsigns?\b|signing|\$\d+(\.\d+)? ?(million|billion|m\b)|trade (rumou?rs?|targets?|candidates?)|"
    r"rumou?rs?|destinations|free agen\w*|mock draft|big board|draft prospects?|fantasy|waivers?|start 'em|sit 'em|"
    r"\bdfs\b|gossip|transfer|keen on|linked with|opinion|column|\bshould\b|grades|winners and losers|"
    r"power rankings?|podcast|newsletter|athlete of the week|\bvote\b|home decor|sweatshirt|\bwears\b|outfit|"
    r"fashion|girlfriend|wedding|mansion|tattoo|livid|hypocrisy|stadium (drama|deal|plans?)|ownership|"
    r"co-owner|suspend\w*|\bfires?\b|\bfired\b|\bhires?\b|\bhired\b|appoint\w*|coaching (bond|carousel|search)|"
    r"part ways|interview|books? we love|recipe|thanksgiving meal|documentary",
    re.I,
)
# Major leagues only: NFL, NBA, NHL, MLB, college football/basketball, soccer.
MINOR_SPORT_RE = re.compile(
    r"/(rugby-union|rugby-league|cricket|motorsport|formula1|golf|tennis|boxing|mma|ufc|nascar|cycling|"
    r"athletics|darts|snooker|horse-racing|olympics|wrestling)/|\b(rugby|cricket|motogp|formula 1|\bF1\b|"
    r"nascar|ufc|mma|golf|tennis|boxing|darts|snooker|wrestling|cycling|atp|wta|pga|lpga|grand prix|ryder cup)\b",
    re.I,
)


# Highlight reels / video clips are game content but rank below written results.
HIGHLIGHT_RE = re.compile(r"/videos?/|\bhighlights\b|best plays|\bwatch\b", re.I)


def sports_tier(story: dict) -> int:
    head = story.get("headline") or ""
    blob = f"{head} {story.get('summary') or ''}"
    url = story.get("url") or ""
    if MINOR_SPORT_RE.search(f"{url} {head}") or OFF_FIELD_RE.search(head):
        return 3
    if HIGHLIGHT_RE.search(f"{url} {head}"):
        return 1
    if GAME_RESULT_RE.search(head):
        return 0
    if GAME_PREVIEW_RE.search(head):
        return 1
    if GAME_RESULT_RE.search(blob) and not OFF_FIELD_RE.search(blob):
        return 1
    return 2


def section_rank_key(section: str):
    """Sort key for a section's candidates (newest first; Sports by game tier first)."""
    def newest(s: dict) -> float:
        raw = s.get("published") or ""
        try:
            return -datetime.fromisoformat(raw).timestamp()
        except ValueError:
            return 0.0
    if section == "sports":
        return lambda s: (sports_tier(s), newest(s))
    return newest


def off_rules(story: dict, section: str) -> bool:
    """Editor (Nov 22): section-specific rules (junk, Sports local/template, TV off-topic)."""
    head = story.get("headline") or ""
    if is_junk(story):
        return True
    if section == "news" and JUNK_NEWS_RE.search(head):
        return True
    if section in ("news", "politics") and is_sports_item(story):
        return True
    if section == "sports" and (
        is_philly(story) or SPORTS_TEMPLATE_RE.search(head) or SPORTS_BETTING_RE.search(head)
    ):
        return True
    if section == "tv" and TV_OFF_RE.search(f"{story.get('url') or ''} {head}"):
        return True
    if section == "movies" and MOVIES_OFF_RE.search(story.get("url") or ""):
        return True
    return False


def is_philly(story: dict) -> bool:
    """Sports-only guard: local Philadelphia-area outlets or local prep roundups (Philly teams themselves are allowed)."""
    blob = f"{story.get('headline') or ''} {story.get('summary') or ''} {story.get('url') or ''}"
    outlet = f"{story.get('url') or ''} {story.get('source') or ''} {story.get('origin') or ''}"
    return bool(
        LOCAL_OUTLET_RE.search(outlet)
        or LOCAL_SPORTS_RE.search(blob)
        or LOCAL_ORIGIN_RE.search(story.get("origin") or "")
    )


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


def headline_key(story: dict) -> str:
    """Editor: same wire story under different URLs (e.g. AP on NPR and ABC)."""
    return "h:" + re.sub(r"\W+", " ", (story.get("headline") or "").lower()).strip()


STOP_WORDS = set("the a an and or of to in on at for with by from as is are was be after over into says say said new its his her their this that".split())


def headline_words(story: dict) -> frozenset:
    words = re.sub(r"[^\w\s]", " ", (story.get("headline") or "").lower()).split()
    return frozenset(w for w in words if len(w) > 2 and w not in STOP_WORDS)


def near_duplicate(a: frozenset, b: frozenset) -> bool:
    """Editor (Nov 21): same story, lightly reworded headline (e.g. CBS vs ABC wire)."""
    small = min(len(a), len(b))
    if small < 4:
        return False
    shared = len(a & b)
    return shared / small >= 0.8 or (shared >= 5 and shared / small >= 0.4)


def is_stale(story: dict, now: datetime | None = None) -> bool:
    raw = story.get("published") or ""
    if not raw:
        return False
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return False
    now = now or datetime.now(ET_ZONE)
    return (now - when).total_seconds() > FRESH_HOURS * 3600


# ---------------------------------------------------------------------------
# Editor (Nov 23): subject-level diversity cap.
# ---------------------------------------------------------------------------
ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9&.\-]*")
# Capitalized words that are not subjects, plus names so common they would
# gut whole sections if capped (the president, Congress, leagues, countries).
TOPIC_STOP = set(
    """
    the a an and or but of to in on at for with by from as is are was were be been it its his her their our your
    this that these those how why what who whom when where which will would can could should may might must
    new after before over under into out up down off about man men woman women city night nation nations
    face did does meet nov dec npr cbs abc bbc cnbc listen lives black auto concert catchup current
    entertainment democratic god than more most not no all any some one two three
    first last next top best big inside watch video live updates update breaking report review reviews exclusive
    says say said told tells ask asks here there just now today tonight week weekend day year years
    monday tuesday wednesday thursday friday saturday sunday january february march april may june july
    august september october november december season episode trailer star stars film films movie movies show
    shows series book album albums tour song songs music news story podcast opinion analysis interview
    ceo president chief former director executive mr mrs ms dr sen rep gov jr sr inc co corp
    war orange tiny desk theft grand spirit fury wives asia lives listen black auto concert catchup
    john david james robert tom nick scott ryan mary ann margaret deborah jason michael chris mike matt
    mark paul peter steve kevin brian jeff jim joe dan tim bill bob george william richard thomas daniel
    sarah jennifer jessica emily elizabeth susan karen lisa laura amy anna kate kim angelo williams jackson
    johnson smith brown jones miller davis wilson moore taylor anderson thomas lee
    trump donald vance biden harris obama us usa u.s america american americans white house congress senate
    democrats democrat republicans republican gop dems federal government state states supreme court
    ai a.i artificial intelligence tech
    nfl nba mlb nhl wnba mls ncaa cfp sec acc big ten premier league college football basketball baseball
    hockey soccer cup world super bowl series championship playoff playoffs game games
    netflix hbo disney hollywood oscar oscars emmy emmys grammy grammys golden globes billboard
    china chinese iran iranian israel israeli gaza ukraine ukrainian russia russian europe european uk britain
    british england canada canadian mexico japan india korea north south east west new york california texas
    washington los angeles san francisco
    alabama alaska arizona arkansas colorado connecticut delaware florida georgia hawaii idaho illinois
    indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi
    missouri montana nebraska nevada hampshire jersey mexico carolina dakota ohio oklahoma oregon
    pennsylvania rhode island tennessee utah vermont virginia wisconsin wyoming
    thanksgiving christmas halloween easter hanukkah award awards tease teases fantasy
    """.split()
)
# Editor (Nov 23, night): US state names, holidays and a few generic words were
# turning unrelated stories into one "subject" (Thanksgiving travel + Bears
# injury report + food costs; Michigan football + an Amazon drone in Michigan;
# North Carolina recount + a cat video), so they are exempt like countries.
DEPART_RE = re.compile(
    r"\b(resign(s|ed)?|steps? down|stepping down|out at|ousted|ouster|fired|exits?|departs?|quits?|leaves|removed)\b",
    re.I,
)
RETURN_RE = re.compile(r"\b(returns?|returned|returning|is back|comes back|reinstat\w*|rehired|comeback)\b", re.I)


def _norm_entity(tok: str) -> str:
    tok = re.sub(r"(['\u2019]s)$", "", tok)
    return tok.strip(".-&").replace(".", "").lower()


def _caps(text: str, vocab: set[str] | None = None) -> set[str]:
    """Capitalized tokens; a sentence-initial word that the corpus also uses in
    lowercase ("Did", "Meet", "How") is not a name."""
    out = set()
    vocab = vocab or set()
    for m in ENTITY_RE.finditer(text or ""):
        tok = _norm_entity(m.group(0))
        if len(tok) < 3 or tok in TOPIC_STOP or tok.isdigit():
            continue
        before = (text[: m.start()]).rstrip()
        initial = not before or before[-1] in ".!?:\"'\u2018\u201c|-\u2014("
        if initial and tok in vocab:
            continue
        out.add(tok)
    return out


LOWER_WORD_RE = re.compile(r"(?<![\w'\u2019])[a-z][a-z0-9\-]+")


def lowercase_vocab(stories: list[dict]) -> set[str]:
    """Words the candidate pool uses in lowercase somewhere (i.e. ordinary words)."""
    vocab: set[str] = set()
    for story in stories:
        vocab.update(LOWER_WORD_RE.findall(f"{story.get('headline') or ''} {story.get('summary') or ''}"))
    return vocab


def story_entities(story: dict, vocab: set[str] | None = None) -> set[str]:
    """Named-entity-ish tokens from the headline and the summary's lead sentence.

    Title-case headlines capitalize every word, so there a token counts only if
    the summary also capitalizes it or the pool never uses it in lowercase."""
    vocab = vocab or set()
    head = story.get("headline") or ""
    summ = story.get("summary") or ""
    words = [w for w in re.findall(r"[A-Za-z][\w'\u2019-]*", head) if len(w) > 3]
    title_case = bool(words) and sum(w[0].isupper() for w in words) / len(words) > 0.6
    head_caps = _caps(head, vocab)
    summ_caps = _caps(summ, vocab)
    if title_case:
        head_caps = {t for t in head_caps if t in summ_caps or t not in vocab}
    lead = SENTENCE_RE.split(summ, 1)[0] if summ else ""
    return head_caps | _caps(lead, vocab)


def hot_subjects(stories: list[dict], vocab: set[str] | None = None) -> set[str]:
    """Entities named in the headlines of >= TOPIC_HOT_MIN distinct candidate stories."""
    counts: dict[str, set[str]] = {}
    for story in stories:
        hk = headline_key(story)
        head_l = (story.get("headline") or "").lower()
        for ent in story_entities(story, vocab):
            if re.search(r"(?<!\w)" + re.escape(ent) + r"(?!\w)", head_l.replace(".", "")):
                counts.setdefault(ent, set()).add(hk)
    return {e for e, keys in counts.items() if len(keys) >= TOPIC_HOT_MIN}


def story_subjects(story: dict, hot: set[str], vocab: set[str] | None = None) -> frozenset:
    return frozenset(story_entities(story, vocab) & hot)


def superseded_keys(stories: list[dict], hot: set[str], vocab: set[str] | None = None) -> set[str]:
    """Headline keys of stories overtaken by a newer development on the same subject.

    1. A departure headline ("Huang resigns", "out at Nvidia") once any story on
       that subject reports a return/reinstatement (feeds often re-stamp old
       items, so publish time alone cannot catch it).
    2. A subject story more than SUPERSEDE_HOURS older than its newest one."""
    by_subject: dict[str, list[dict]] = {}
    for story in stories:
        for ent in story_subjects(story, hot, vocab):
            by_subject.setdefault(ent, []).append(story)
    out: set[str] = set()
    for members in by_subject.values():
        heads = [m.get("headline") or "" for m in members]
        reversal = any(RETURN_RE.search(h) for h in heads)
        newest = max((m.get("published") or "" for m in members), default="")
        newest_dt = None
        try:
            newest_dt = datetime.fromisoformat(newest) if newest else None
        except ValueError:
            pass
        for m in members:
            head = m.get("headline") or ""
            if reversal and DEPART_RE.search(head) and not RETURN_RE.search(head):
                out.add(headline_key(m))
                continue
            raw = m.get("published") or ""
            if newest_dt and raw:
                try:
                    age = (newest_dt - datetime.fromisoformat(raw)).total_seconds()
                except ValueError:
                    continue
                if age > SUPERSEDE_HOURS * 3600:
                    out.add(headline_key(m))
    return out


RUN_RE = re.compile(r"[A-Z][\w.\-]*(?:['\u2019]s)?(?:\s+[A-Z][\w.\-]*(?:['\u2019]s)?)*")


def headline_anchors(story: dict, vocab: set[str] | None = None) -> frozenset:
    """Last word of each capitalized run in the headline when it is a real name
    (never used in lowercase by the pool): "Rep. Jimmy Gomez" -> gomez. Two
    stories in one section that share one are about the same subject even if
    the subject is not hot (e.g. two Gomez resignation stories)."""
    vocab = vocab or set()
    out = set()

    def name_like(tok: str) -> bool:
        return len(tok) >= 3 and tok not in TOPIC_STOP and tok not in vocab and not tok.isdigit()

    for m in RUN_RE.finditer(story.get("headline") or ""):
        words = [_norm_entity(w) for w in m.group(0).split()]
        if name_like(words[-1]):
            out.add(words[-1])
        # Editor (Nov 23, night): title-case headlines make the capitalized run
        # end on an ordinary word ("Bela Bajaria Calls Time ..."), so also use
        # any two adjacent name-like words ("bela bajaria"). Two Bajaria stories
        # sat together in Media for two runs on Nov 23.
        for a, b in zip(words, words[1:]):
            if name_like(a) and name_like(b):
                out.add(a + " " + b)
        # Editor (Nov 24): a title-case headline that opens with a one-word name
        # ("Ninajirachi Drops ...") gives no last-word or pair anchor, so also use
        # a long (6+ letters) name-like first word. Pitchfork and Rolling Stone ran
        # the same Ninajirachi album together in Music for 7 runs on Nov 24.
        if len(words[0]) >= 6 and name_like(words[0]):
            out.add(words[0])
    return frozenset(out)


class TopicBudget:
    """Edition-wide subject counter shared by all sections (site order)."""

    def __init__(self, hot: set[str], superseded: set[str], vocab: set[str] | None = None):
        self.hot = hot
        self.superseded = superseded
        self.vocab = vocab or set()
        self.edition: dict[str, int] = {}

    def subjects(self, story: dict) -> frozenset:
        return story_subjects(story, self.hot, self.vocab)

    def allows(self, story: dict, section_counts: dict[str, int], edition_cap: int) -> bool:
        if headline_key(story) in self.superseded:
            return False
        for anchor in headline_anchors(story, self.vocab):
            if section_counts.get("@" + anchor, 0) >= TOPIC_MAX_PER_SECTION:
                return False
        for ent in self.subjects(story):
            if section_counts.get(ent, 0) >= TOPIC_MAX_PER_SECTION:
                return False
            if self.edition.get(ent, 0) >= edition_cap:
                return False
        ents = story_entities(story, self.vocab)
        for other in section_counts.get("__ents", []):
            if len(ents & other) >= TOPIC_SHARED_ENTITIES:
                return False
        return True

    def take(self, story: dict, section_counts: dict[str, int]) -> None:
        for anchor in headline_anchors(story, self.vocab):
            section_counts["@" + anchor] = section_counts.get("@" + anchor, 0) + 1
        for ent in self.subjects(story):
            section_counts[ent] = section_counts.get(ent, 0) + 1
            self.edition[ent] = self.edition.get(ent, 0) + 1
        section_counts.setdefault("__ents", []).append(story_entities(story, self.vocab))


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
            "origin": item.get("origin") or "",
        }
        if prefer and not url_matches(story["url"], prefer):
            continue
        if ai_only and not looks_like_ai(story):
            continue
        if off_rules(story, feed["section"]):
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
                    "origin": item.get("origin") or "",
                }
                if off_rules(story, feed["section"]):
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
    used_words: list[frozenset] | None = None,
    topics: "TopicBudget | None" = None,
) -> list[dict]:
    ranked = sorted(candidates, key=lambda s: s.get("published") or "", reverse=True)
    used = used if used is not None else set()
    used_words = used_words if used_words is not None else []
    seen_words: list[frozenset] = []
    seen: set[str] = set()
    picked: list[dict] = []
    per_source: dict[str, int] = {}
    cap = [MAX_PER_SOURCE]
    sec_topics: dict[str, int] = {}
    topic_cap = [TOPIC_MAX_PER_EDITION]

    def consider(story: dict, already_verified: bool) -> None:
        if len(picked) >= MAX_PER_SECTION:
            return
        key = story_key(story)
        hkey = headline_key(story)
        if not key or key in seen or key in used or hkey in seen or hkey in used:
            return
        words = headline_words(story)
        if any(near_duplicate(words, w) for w in seen_words + used_words):
            return
        if per_source.get(story.get("source") or "", 0) >= cap[0]:
            return
        if off_rules(story, section_id):
            return
        if topics is not None and not topics.allows(story, sec_topics, topic_cap[0]):
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
        seen.add(hkey)
        seen_words.append(words)
        per_source[clean["source"]] = per_source.get(clean["source"], 0) + 1
        if topics is not None:
            topics.take(story, sec_topics)
        picked.append(clean)

    # Editor (Nov 21): fresh stories first (capped, then cap lifted), and only
    # then older ones, so a slow feed's two-day-old item cannot hold a slot.
    if section_id == "sports":
        ranked = sorted(candidates, key=section_rank_key("sports"))
    fresh = [s for s in ranked if not is_stale(s)]
    stale = [s for s in ranked if is_stale(s)]
    if section_id == "music":
        # Editor (Nov 25): evergreen retrospective lists rank after real news.
        evergreen = [s for s in fresh if MUSIC_EVERGREEN_RE.search(s.get("headline") or "")]
        fresh = [s for s in fresh if s not in evergreen]
        stale = stale + evergreen
    passes = ((fresh, False), (fresh, True), (stale, False), (stale, True))
    if section_id == "sports":
        # Editor (Nov 23): games (results, previews, stakes, on-field news) from
        # the last 36h, then older games, and only then off-field / minor sports.
        # Within each tier the per-source cap is 2, then 3, so real results
        # are not crowded out by previews just because one wire has most of them.
        passes = []
        for tier in (0, 1, 2):
            group = [s for s in fresh if sports_tier(s) == tier]
            passes += [(group, MAX_PER_SOURCE), (group, MAX_PER_SOURCE + 1)]
        main_s = [s for s in stale if sports_tier(s) < 3]
        off = [s for s in fresh + stale if sports_tier(s) >= 3]
        passes += [(main_s, MAX_PER_SOURCE), (main_s, True), (off, MAX_PER_SOURCE), (off, True)]
    for pool, lifted in passes:
        if lifted is True or lifted is False:
            cap[0] = MAX_PER_SECTION if lifted else MAX_PER_SOURCE
        else:
            cap[0] = lifted
        for story in pool:
            url = (story.get("url") or "").strip()
            if url and url in verified:
                consider(story, True)
    # If we don't have enough, fill from previous verified/unverified-ok previous stories
    if len(picked) < MIN_FRESH:
        for story in previous:
            consider(story, True)
    # Editor (Nov 23): only if the section is still short, let a hot subject
    # past its edition cap (still at most one per section; superseded stays out).
    if len(picked) < MAX_PER_SECTION and topics is not None:
        topic_cap[0] = 10**6
        for pool in (fresh, stale):
            cap[0] = MAX_PER_SECTION
            for story in pool:
                url = (story.get("url") or "").strip()
                if url and url in verified:
                    consider(story, True)
    picked = picked[:MAX_PER_SECTION]
    used.update(story_key(s) for s in picked)
    used.update(headline_key(s) for s in picked)
    used_words.extend(headline_words(s) for s in picked)
    return picked


def build_sections(day: dict, by_section: dict[str, list[dict]], verified: set[str], failed: set[str]) -> list[dict]:
    sections = []
    posts = existing_posts(day)
    used: set[str] = set()  # Editor: no story appears in two sections
    used_words: list[frozenset] = []
    pool = [st for sec in STORY_SECTIONS for st in (by_section.get(sec) or []) + existing_stories(day, sec)]
    vocab = lowercase_vocab(pool)
    hot = hot_subjects(pool, vocab)
    topics = TopicBudget(hot, superseded_keys(pool, hot, vocab), vocab)
    if hot:
        print("Hot subjects (capped): " + ", ".join(sorted(hot)))
    for sec_id in SECTION_ORDER:
        if sec_id == "x":
            if posts:
                sections.append({"id": "x", "title": "On X", "stories": [], "posts": posts})
            continue
        previous = existing_stories(day, sec_id)
        stories = pick_section(
            by_section.get(sec_id) or [], previous, verified, failed, used, sec_id, used_words, topics
        )
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
            ranked = sorted(by_section[sec_id], key=section_rank_key(sec_id))
            limit = 8 if sec_id == "sports" else 4
            for story in ranked:
                url = story.get("url") or ""
                src = story.get("source") or ""
                if not url or url in seen_check or per_src.get(src, 0) >= limit:
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
