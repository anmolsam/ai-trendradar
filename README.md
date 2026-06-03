# AI TrendRadar

A daily-updated **web page** of trending GitHub repos + fresh arXiv papers,
filtered to English-only content about: AI advancement, AI agents, MCP servers,
frontier labs (Anthropic/OpenAI/DeepMind/Mistral/…), and FAANG AI.

Published free via **GitHub Pages**. Every day a new dated page is generated and
the homepage shows the latest, with an archive list of all past days.

**Live site:** https://anmolsam.github.io/ai-trendradar/

## Sources (ranked by signal)

1. **Top labs & institutes** — RSS from OpenAI, Google DeepMind, Google Research,
   Microsoft Research, NVIDIA, Apple ML, Hugging Face, Together AI, AWS ML +
   Berkeley BAIR, CMU ML, MIT, Stanford SAIL. Labs without a feed (Anthropic,
   Meta, Mistral, DeepSeek, xAI) come via targeted Google News searches.
2. **Hugging Face Daily Papers** — ranked by community **upvotes** (best paper signal).
3. **Trending GitHub repos** — `github.com/trending` (star velocity) + GitHub
   Search API (total stars), ranked mcpmarket-style by stars-gained-today.
4. **Fresh arXiv papers** — `cs.AI/cs.CL/cs.LG/cs.MA` RSS, weighted toward
   frontier-lab / agent / MCP topics.

Everything is theme-filtered (`config.yaml`), English-only, recency-capped,
per-source capped, and **never repeated** (see the seen-ledger below). Rendered
to a dark-themed page in `docs/`; a GitHub Actions cron commits it daily — no
server, no email.

## Never-repeat ledger

`state/seen.json` records every repo / paper / post ever published. Each run
excludes anything already in it, then appends today's items and commits the
ledger back. So a repo or paper appears **once, ever** — a new day always brings
new content.

## How it publishes

**Today only — no history.** Each run fully replaces `docs/index.html` with the
current day's content; any old dated pages are deleted. GitHub Pages serves the
`docs/` folder on the `main` branch. The seen-ledger guarantees nothing repeats,
so "today only" is always genuinely new.

## Setup (already done for this repo)

1. **Enable Pages:** Settings → Pages → Source = "Deploy from a branch",
   Branch = `main`, Folder = `/docs`.
2. **Schedule:** runs daily at **09:00 IST (03:30 UTC)** via
   `.github/workflows/daily-digest.yml`. Trigger manually anytime from the
   **Actions** tab → "Run workflow".

**Today-only:** every section is windowed to fresh content (news/repos = last
day, arXiv = latest announcement batch, HF = last few days while upvotes mature)
and the seen-ledger guarantees nothing repeats — so each 9am run is all-new. The
page also carries `<meta http-equiv="refresh" content="3600">`, so a tab left
open re-pulls the latest hourly.

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
