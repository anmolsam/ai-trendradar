# AI TrendRadar

Daily email digest of **trending GitHub repos + fresh arXiv papers**, filtered to
English-only content about: AI advancement, AI agents, MCP servers, frontier labs
(Anthropic/OpenAI/DeepMind/Mistral/…), and FAANG AI.

Runs on a free GitHub Actions cron and emails you via Gmail SMTP. No server needed.

## What it does

- **GitHub repos** — scrapes `github.com/trending` (daily) *and* runs targeted
  GitHub Search API queries for recently-created, high-star repos.
- **arXiv papers** — pulls the latest announcements from `cs.AI`, `cs.CL`, `cs.LG`,
  `cs.MA` via arXiv's RSS feeds.
- Filters everything by the keyword themes in `config.yaml`, drops non-English
  (CJK-heavy) items, dedupes, and emails a clean HTML digest.

## Setup

### 1. Push to GitHub
```bash
cd ai-trendradar
gh repo create ai-trendradar --private --source=. --remote=origin --push
# or: create a repo on github.com and `git push -u origin main`
```

### 2. Generate a Gmail app password
1. Enable 2-Step Verification on your Google account.
2. Go to https://myaccount.google.com/apppasswords → create an app password.
3. Copy the 16-character password.

### 3. Add repo secrets
In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|--------|-------|
| `GMAIL_USER` | your full Gmail address (the sender) |
| `GMAIL_APP_PASSWORD` | the 16-char app password from step 2 |

`GITHUB_TOKEN` is provided automatically by Actions — no setup needed.

### 4. Run it
- Manual: **Actions tab → "AI TrendRadar daily digest" → Run workflow**.
- Automatic: runs daily at **08:00 IST (02:30 UTC)**. Change the cron in
  `.github/workflows/daily-digest.yml`.

## Tuning

Everything is in **`config.yaml`** — no code changes needed:
- `email.to` — recipients (currently `anmol@attentive.ai`).
- `themes` — keyword groups; an item is kept if it matches **any** keyword.
- `limits` — max repos/papers per email.
- `github_search_queries` — extra GitHub Search queries.
- `arxiv_categories` — which arXiv categories to pull.

## Local test (no email sent)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python trendradar.py --dry-run   # writes digest_preview.html
```
