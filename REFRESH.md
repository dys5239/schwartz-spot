# Refresh runbook (about 30 minutes)

The live site is [The Schwartz Spot](https://dys5239.github.io/schwartz-spot/). This folder is a static GitHub Pages site. A refresh updates today's edition (the date rolls over at midnight Eastern time), then commits and pushes only if something changed. Do not invent headlines, summaries, or URLs.

## 1. Fetch On X (needs the X connector)

`scripts/fetch_x.py` cannot call X by itself. In this bot, use the `x` MCP tools:

1. Look at the 14 accounts in `ACCOUNTS` inside `scripts/fetch_x.py`. If a handle has not been checked recently, confirm it with `get_users_by_usernames` (`verified`, `verified_type`, `created_at`, `public_metrics`, `description`, `name`). Skip any account that is not clearly the real, verified one.
2. For each confirmed account, call `get_users_posts` with the numeric user id. Exclude replies and retweets. Use a `start_time` about 24 hours ago and `max_results` 25. If an account has fewer than three original or quote posts in that window, fetch again without `start_time` and take the latest 3–5.
3. Save each raw response as `data/x-raw/YYYY-MM-DD/<handle>.json`. That directory is gitignored; keep the dumps on the box only.
4. Merge them into the edition (this also stamps `updatedAt` and updates `data/index.json`):

```bash
DATE=$(TZ=America/New_York date +%F)
mkdir -p "data/x-raw/$DATE"
python3 scripts/fetch_x.py --date "$DATE" --raw-dir "data/x-raw/$DATE"
```

If today's edition file does not exist yet, run the RSS step first (it will create the day), then merge On X.

## 2. Fetch mainstream news and sports (automatic)

```bash
python3 scripts/refresh_feeds.py
```

This pulls the RSS/Atom feeds listed in the script, keeps at most five stories per section, and keeps a link only after a live fetch of the article URL succeeds. On X posts already on the day are left alone.

Selection rules (editor, Nov 19, 2026): a story appears in only one section (first in site order wins, so News beats Politics and TV beats Media); at most two stories per source per section unless the section would otherwise be short; the newest four links per source per section are verified; shopping/deal roundups and betting promo-code posts are dropped; Sports drops stories from local Philadelphia-area outlets and high-school/prep roundups; national coverage of Philly teams is allowed (changed Nov 20, 2026 at Dave's request). Gzip-encoded feeds (FOX News Sports) are decompressed automatically. Editor, Nov 20, 2026: the same headline under different URLs (wire stories) is also de-duplicated across sections; TV uses Deadline's TV-only feed; Media uses The Hollywood Reporter's business feed instead of Deadline's unfiltered main feed, and Variety's Media filter prefers only `/biz/` and `/digital/`. In On X, an account's replies to its own posts (thread continuations) count as original posts; replies to others and retweets are still dropped. Editor, Nov 21, 2026: stories published within the last 36 hours are picked before older ones (so a slow feed's two-day-old item no longer holds a slot all day); near-duplicate headlines (same story reworded by another outlet, e.g. CBS and ABC wire versions) are also de-duplicated within and across sections; Media now uses the business-category feeds of THR, Variety and Deadline (CBS entertainment dropped from Media); Variety "Strictly Business" sponsored pieces and betting-app bonus posts are dropped as junk. Editor, Nov 22, 2026: TV uses the TV-category feeds of Deadline, Variety (`https://variety.com/v/tv/feed/`, replacing Variety's main feed whose fallback leaked music/film) and The Hollywood Reporter (`https://www.hollywoodreporter.com/c/tv/feed/`, replacing ABC News entertainment); TV also drops film/music URLs and box-office stories; News drops BBC "The Papers" press reviews and CBS "This week on Sunday Morning" promos; Sports drops betting odds/picks/prediction posts and AP automated small-game score templates (e.g. "X secures 92-67 win over Y", "Smith scores 23, X downs Y 114-85"). These are format filters, not team filters — national coverage of Philly teams still passes. Editor, Nov 23, 2026 (Dave: too many Jensen Huang / Nvidia stories — 9 of 45 slots): **subject-level diversity cap**. A "hot subject" is a named entity (person, company, team, show) that appears in the headlines of at least 3 distinct candidate stories that run; very common names (Trump, Congress, U.S., leagues, countries, networks) are exempt. Stories sharing a hot subject (headline or the summary's lead sentence) are limited to **1 per section and 2 per edition**; the freshest wins and the slot is backfilled from the next-best story. Within a section, two headlines that share a specific surname/brand (e.g. two "Gomez resigns" stories) also count as the same subject. **Superseded stories are dropped**: a departure headline ("resigns", "steps down", "out at", "ousted") once any story on that subject reports a return/reinstatement, and any subject story more than 24 hours older than that subject's newest one. Only if a section would otherwise be short may a hot subject exceed the edition cap (still 1 per section). The script prints `Hot subjects (capped): …` on every run. Settings: `TOPIC_HOT_MIN`, `TOPIC_MAX_PER_SECTION`, `TOPIC_MAX_PER_EDITION`, `SUPERSEDE_HOURS`, `TOPIC_STOP` in `scripts/refresh_feeds.py`. On X is not capped (it shows the verified accounts' own posts).

Feeds verified with curl from this box on Thu Nov 19, 2026 (America/New_York):

**News:** NPR `https://feeds.npr.org/1001/rss.xml`, CBS News `https://www.cbsnews.com/latest/rss/main`, ABC News `https://abcnews.go.com/abcnews/topstories`, BBC News `https://feeds.bbci.co.uk/news/rss.xml`

**Politics:** NPR `https://feeds.npr.org/1014/rss.xml`, CBS News `https://www.cbsnews.com/latest/rss/politics`, ABC News `https://abcnews.go.com/abcnews/politicsheadlines`, Politico `https://rss.politico.com/politics-news.xml`

**Economy:** NPR `https://feeds.npr.org/1006/rss.xml`, CBS News `https://www.cbsnews.com/latest/rss/moneywatch`, ABC News `https://abcnews.go.com/abcnews/businessheadlines`, CNBC `https://www.cnbc.com/id/100003114/device/rss/rss.html`

**Sports:** FOX News Sports `https://feeds.foxnews.com/foxnews/sports`, CBS Sports `https://www.cbssports.com/rss/headlines/`, Yahoo Sports `https://sports.yahoo.com/rss/`, ABC News `https://abcnews.go.com/abcnews/sportsheadlines`, NPR `https://feeds.npr.org/1008/rss.xml`, CBS News `https://www.cbsnews.com/latest/rss/sports`, BBC Sport `https://feeds.bbci.co.uk/sport/rss.xml`

**Movies / TV / Media / Music:** NPR movies `https://feeds.npr.org/1045/rss.xml`, Variety `https://variety.com/feed/`, The Hollywood Reporter `https://www.hollywoodreporter.com/feed/`, Deadline TV `https://deadline.com/v/tv/feed/`, THR business `https://www.hollywoodreporter.com/c/business/feed/` (both added Nov 20, 2026), Variety business `https://variety.com/v/biz/feed/` and Deadline business `https://deadline.com/v/business/feed/` (Media, added Nov 21, 2026), Variety TV `https://variety.com/v/tv/feed/` and THR TV `https://www.hollywoodreporter.com/c/tv/feed/` (TV, added Nov 22, 2026; ABC News entertainment dropped), NPR music `https://feeds.npr.org/1039/rss.xml`, Billboard `https://www.billboard.com/feed/`, Pitchfork `https://pitchfork.com/feed/feed-news/rss`, Rolling Stone `https://www.rollingstone.com/music/music-news/feed/`

**AI (keyword-filtered tech feeds):** TechCrunch `https://techcrunch.com/feed/`, The Verge `https://www.theverge.com/rss/index.xml`, Wired `https://www.wired.com/feed/rss`, CBS News technology `https://www.cbsnews.com/latest/rss/technology`, ABC News technology `https://abcnews.go.com/abcnews/technologyheadlines`, NPR technology `https://feeds.npr.org/1019/rss.xml`

**Tried and not used:** ESPN `https://www.espn.com/espn/rss/news` (HTTP 202, empty body), official FOX Sports / FS1 RSS and the FOX Sports API (404 or invalid partner key), AP News RSS (403), Reuters public RSS (TLS / 401 / 404).

Optional: `python3 scripts/refresh_feeds.py --dry-run` prints the planned slate without writing. `--date YYYY-MM-DD` overrides the Eastern-time date.

## 3. Review the day file

Skim `data/YYYY-MM-DD.json`. Check that no subject has more than one story per section or two in the edition (the nightly editor enforces this; if a cluster slips through, e.g. a name used in different forms, add an alias or tighten the rule rather than hand-editing), that summaries are 1–2 sentences, links look right, Sports stays national, and On X still has only the verified accounts. The masthead will show `Updated h:mm AM ET` from the file's `updatedAt` field. An open browser tab re-fetches `data/*.json` every three minutes (network-first in the service worker), so a push should appear without a hard reload.

## 4. Commit and push only if something changed

```bash
cd /workspace/schwartz-spot
git add data/index.json data/*.json
git status
# If there are staged content changes:
git -c user.name=dys5239 -c user.email=dys5239@users.noreply.github.com \
  commit -m "Refresh edition for $(TZ=America/New_York date +%F)"
git push origin main
```

Do not commit `data/x-raw/` (it is gitignored). If `git status` is clean after the refresh, stop; there is nothing to push.

`scripts/refresh.sh` runs steps 2 and 4 (and step 1's merge, when a raw directory is already on disk). It will not push when the tree is unchanged.
