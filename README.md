# AI TrendRadar

A daily-updated **web page** of trending GitHub repos + fresh arXiv papers,
filtered to English-only content about: AI advancement, AI agents, MCP servers,
frontier labs (Anthropic/OpenAI/DeepMind/Mistral/…), and FAANG AI.

Published free via **GitHub Pages**. Every day a new dated page is generated and
the homepage shows the latest, with an archive list of all past days.

**Live site:** https://anmolsam.github.io/ai-trendradar/

## What it does

- **GitHub repos** — scrapes `github.com/trending` (daily) *and* runs targeted
  GitHub Search API queries for recently-created, high-star repos.
- **arXiv papers** — pulls the latest announcements from `cs.AI`, `cs.CL`, `cs.LG`,
  `cs.MA` via arXiv's RSS feeds.
- Filters by the keyword themes in `config.yaml`, drops non-English (CJK-heavy)
  items, dedupes, and renders a dark-themed HTML page into `docs/`.
- A GitHub Actions cron commits the new pages back daily — no server, no email.

## How it publishes

Each run writes:
- `docs/<YYYY-MM-DD>.html` — that day's archived snapshot.
- `docs/index.html` — the latest digest + an **Archive** list linking every past day.

GitHub Pages serves the `docs/` folder on the `main` branch.

## Setup (already done for this repo)

1. **Enable Pages:** Settings → Pages → Source = "Deploy from a branch",
   Branch = `main`, Folder = `/docs`.
2. **Schedule:** runs daily at **08:00 IST (02:30 UTC)** via
   `.github/workflows/daily-digest.yml`. Trigger manually anytime from the
   **Actions** tab → "Run workflow".

The workflow uses the auto-provided `GITHUB_TOKEN` only — no secrets to set.

## Tuning

Everything is in **`config.yaml`** — no code changes needed:
- `themes` — keyword groups; an item is kept if it matches **any** keyword.
- `limits` — max repos/papers per page.
- `github_search_queries` — extra GitHub Search queries.
- `arxiv_categories` — which arXiv categories to pull.

## Local run
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python trendradar.py        # writes docs/index.html + docs/<date>.html
open docs/index.html
```
