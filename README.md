# The Schwartz Spot

A personal news site — The Schwartz Spot — for Dave Schwartz. It shows that day’s stories in nine sections, plus an archive of earlier editions.

The sections, in order, are **AI**, **News**, **On X**, **Politics**, **Economy**, **Sports**, **Movies**, **TV**, **Media**, and **Music**. Sports covers national headlines — NFL, NBA, MLB, NHL, college, and the like — not coverage of a particular city’s teams. There is no jobs section.

This is a static site: plain HTML, CSS, and JavaScript, with no build step. It is also a progressive web app, so it can be added to an iPhone home screen and still be readable offline after the first visit. A sticky row of section chips at the top of the page jumps to each section.

Nothing here has been published yet. The files live on this computer only, under `/workspace/schwartz-spot`. Do not create a GitHub repo, push, or deploy until Dave says so.

## What’s on the page

- A masthead reading **The Schwartz Spot**, plus the date of the edition being read.
- A horizontal, sticky section bar for hopping around on a phone.
- **On X**: a chronological rundown of recent posts from a fixed list of verified accounts, with Eastern timestamps and a link back to each post on X. Post text is escaped, never rendered as HTML.
- Each story has a headline, a short summary, the source, and a link when one is known.
- An **Archive** of earlier editions. Older days may include only the AI section, from earlier morning briefs.
- Light and dark mode, following the phone’s setting unless you override it with the toggle.

## Preview it locally

From this folder:

```bash
python3 -m http.server 8080
```

Then open [http://localhost:8080](http://localhost:8080). On an iPhone on the same network, use this computer’s address instead of localhost.

### Add it to an iPhone home screen

1. Open the site in Safari.
2. Tap the Share button.
3. Choose **Add to Home Screen**.

The app icon is a bold **ΣΣ** mark (two capital Greek sigmas). After that, it opens like a small app and can be read offline once a day’s edition has been loaded at least once.

## Add a new edition

Save the edition as JSON and run:

```bash
python3 scripts/add_day.py path/to/2026-11-20.json
```

Or pipe it in:

```bash
python3 scripts/add_day.py --stdin < path/to/brief.json
```

The script writes `data/YYYY-MM-DD.json` and updates `data/index.json`. Omit any section that has no stories. Leave unknown fields as empty strings; do not invent a headline, summary, or link.

A brief looks like this:

```json
{
  "date": "2026-11-20",
  "sections": [
    {
      "id": "ai",
      "title": "AI",
      "stories": [
        {
          "headline": "Example headline",
          "summary": "One or two sentences, with no extra claims.",
          "source": "The Rundown",
          "url": "https://example.com/story"
        }
      ]
    }
  ]
}
```

Allowed section ids, in site order: `ai`, `news`, `x`, `politics`, `economy`, `sports`, `movies`, `tv`, `media`, `music`. The `x` section uses a `posts` array (see `scripts/fetch_x.py`).

## Seeded editions

| Date | What’s in it |
| --- | --- |
| Thu Nov 19, 2026 | Full edition, drawn from reporting published around Nov 18–19, 2026. Every story has a source and a verified article URL. The On X section is a verified-account rundown from the same morning. |
| Wed Nov 18, Mon Nov 16, Sat Nov 8, Sat Oct 31, Fri Oct 30, 2026 | AI stories only, kept from earlier morning-brief notes. Most of those items still have no article links. |

## Live site

The site is published at [https://dys5239.github.io/schwartz-spot/](https://dys5239.github.io/schwartz-spot/). The public repository is [https://github.com/dys5239/schwartz-spot](https://github.com/dys5239/schwartz-spot). GitHub Pages is served from the `main` branch root.

The masthead shows an `Updated h:mm AM ET` stamp. An open tab re-fetches the day file every three minutes; the service worker uses network-first for `data/*.json` so a push shows up without a hard reload.

To refresh an edition, follow [REFRESH.md](REFRESH.md). The usual path is the X connector plus `python3 scripts/refresh_feeds.py`, then commit and push only if something changed (`scripts/refresh.sh` can do the RSS merge and the push).

## Files

- `config.js` — the display title and short mark, in one place. Change these when a new title is chosen; also update `manifest.json`, this README, and `scripts/add_day.py`.
- `index.html`, `styles.css`, `app.js` — the site.
- `manifest.json`, `sw.js`, `icons/`, `apple-touch-icon.png` — home-screen app and offline cache.
- `data/index.json` and `data/YYYY-MM-DD.json` — the editions.
- `scripts/add_day.py` — append or replace a day.
- `scripts/fetch_x.py` — merge fetched X timelines into the On X section. Live fetching needs the X connector; the script only merges saved responses.
- `scripts/refresh_feeds.py` — pull verified public RSS feeds into today's news sections.
- `scripts/refresh.sh` — run the RSS refresh (and On X merge if raw dumps are present), then commit and push only if data changed.
- `REFRESH.md` — the 30-minute refresh runbook, including which RSS feeds work from this box.
