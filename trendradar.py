#!/usr/bin/env python3
"""
AI TrendRadar — daily digest of trending GitHub repos + arXiv papers
on AI advancement, AI agents, MCP servers, frontier labs, and FAANG.

Runs in GitHub Actions; publishes a daily HTML page to GitHub Pages (docs/).
Each run writes docs/<YYYY-MM-DD>.html and rebuilds docs/index.html (latest +
an archive list of every past day). Config lives in config.yaml.
"""

import os
import re
import sys
import glob
import html
import json
import time
import datetime as dt

import requests
import feedparser
import yaml
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "ai-trendradar/1.0 (+https://github.com/anmolsam)"}


# ---------------------------------------------------------------- config ----
def load_config():
    with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def all_keywords(cfg):
    kws = []
    for group in cfg["themes"].values():
        kws.extend(k.strip().lower() for k in group)
    return kws


# --------------------------------------------------- persistent seen-ledger -
# Every repo/paper we've ever PUBLISHED is recorded here so it never repeats
# on a future day. Committed back to the repo each run (state/seen.json).
SEEN_PATH = os.path.join(ROOT, "state", "seen.json")


def load_seen():
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return {
        "github": set(data.get("github", [])),
        "arxiv": set(data.get("arxiv", [])),
    }


def save_seen(seen):
    os.makedirs(os.path.dirname(SEEN_PATH), exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"github": sorted(seen["github"]), "arxiv": sorted(seen["arxiv"])},
            f,
            indent=0,
        )


# ------------------------------------------------------------- filtering ----
_CJK = re.compile(
    r"[一-鿿぀-ヿ가-힯؀-ۿЀ-ӿ]"
)


def is_english(text):
    """Drop strings that are predominantly non-Latin (CJK, Arabic, Cyrillic)."""
    if not text:
        return True
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    non_latin = sum(1 for c in letters if _CJK.match(c))
    return (non_latin / len(letters)) < 0.30


def matched_themes(text, cfg):
    """Return the set of theme names whose keywords appear in text."""
    t = (text or "").lower()
    hits = set()
    for name, group in cfg["themes"].items():
        for kw in group:
            if kw.strip().lower() in t:
                hits.add(name)
                break
    return hits


# --------------------------------------------------------------- github -----
def fetch_github_trending(cfg):
    """Scrape github.com/trending (daily, all languages)."""
    repos = []
    try:
        r = requests.get(
            "https://github.com/trending?since=daily", headers=UA, timeout=30
        )
        r.raise_for_status()
    except Exception as e:
        print(f"[github trending] fetch failed: {e}", file=sys.stderr)
        return repos

    soup = BeautifulSoup(r.text, "html.parser")
    for row in soup.select("article.Box-row"):
        a = row.select_one("h2 a")
        if not a:
            continue
        full_name = re.sub(r"\s+", "", a.get_text(strip=True))
        url = "https://github.com" + a.get("href", "")
        desc_el = row.select_one("p")
        desc = desc_el.get_text(strip=True) if desc_el else ""
        lang_el = row.select_one('[itemprop="programmingLanguage"]')
        lang = lang_el.get_text(strip=True) if lang_el else ""
        stars_today = ""
        velocity = 0
        star_el = row.select_one("span.d-inline-block.float-sm-right")
        if star_el:
            stars_today = star_el.get_text(strip=True)
            m = re.search(r"([\d,]+)", stars_today)
            if m:
                velocity = int(m.group(1).replace(",", ""))
        repos.append(
            {
                "full_name": full_name,
                "url": url,
                "desc": desc,
                "lang": lang,
                "stars": stars_today,
                "velocity": velocity,   # stars gained today (mcpmarket "Top Today")
                "stars_total": 0,
                "source": "trending",
            }
        )
    print(f"[github trending] scraped {len(repos)} repos", file=sys.stderr)
    return repos


def fetch_github_search(cfg):
    """Use the GitHub Search API for recently-created, high-star repos per query."""
    repos = []
    token = os.environ.get("GITHUB_TOKEN")
    headers = dict(UA)
    headers["Accept"] = "application/vnd.github+json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    since = (
        dt.datetime.now(dt.timezone.utc)
        - dt.timedelta(days=cfg["lookback_days"]["github"])
    ).strftime("%Y-%m-%d")

    for query in cfg.get("github_search_queries", []):
        q = f"{query} created:>{since}"
        try:
            r = requests.get(
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "stars", "order": "desc", "per_page": 10},
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            items = r.json().get("items", [])
        except Exception as e:
            print(f"[github search] '{query}' failed: {e}", file=sys.stderr)
            continue
        for it in items:
            total = it.get("stargazers_count", 0)
            repos.append(
                {
                    "full_name": it["full_name"],
                    "url": it["html_url"],
                    "desc": it.get("description") or "",
                    "lang": it.get("language") or "",
                    "stars": f"{total:,} stars",
                    "velocity": 0,
                    "stars_total": total,
                    "source": "search",
                }
            )
    print(f"[github search] collected {len(repos)} repos", file=sys.stderr)
    return repos


def collect_github(cfg, seen):
    """Collect, theme-filter, drop already-published repos, rank by 'amazing'."""
    picked = {}
    for repo in fetch_github_trending(cfg) + fetch_github_search(cfg):
        key = repo["full_name"]
        if key in seen["github"] or key in picked:
            continue  # never repeat a repo we've published before
        text = f"{key} {repo['desc']}"
        if cfg.get("english_only", True) and not is_english(text):
            continue
        themes = matched_themes(text, cfg)
        if not themes:
            continue
        repo["themes"] = sorted(themes)
        picked[key] = repo
    out = list(picked.values())
    # "Most amazing" = mcpmarket-style: star velocity (today) first, then total
    # stars, then how many themes it hits.
    out.sort(
        key=lambda r: (r["velocity"], r["stars_total"], len(r["themes"])),
        reverse=True,
    )
    return out[: cfg["limits"]["github"]]


# ---------------------------------------------------------------- arxiv -----
# Theme weights for the paper "amazing" score — brand-new papers have no
# citations on day 1, so we proxy quality by topic salience.
ARXIV_THEME_WEIGHT = {
    "frontier_labs": 3,
    "mcp": 3,
    "ai_agents": 2,
    "ai_advancement": 1,
    "faang": 1,
}


def arxiv_id(link):
    m = re.search(r"abs/([\d.]+)", link or "")
    return m.group(1) if m else link


def fetch_arxiv(cfg, seen):
    cats = cfg.get("arxiv_categories", [])
    if not cats:
        return []
    # Use arXiv's lightweight per-category RSS feeds (latest announcements).
    entries = []
    for cat in cats:
        url = f"https://rss.arxiv.org/rss/{cat}"
        for attempt in range(3):
            try:
                resp = requests.get(url, headers=UA, timeout=30)
                if resp.status_code == 429:
                    time.sleep(4 * (attempt + 1))
                    continue
                resp.raise_for_status()
                entries.extend(feedparser.parse(resp.content).entries)
                break
            except Exception as e:
                print(f"[arxiv] {cat} attempt {attempt} failed: {e}", file=sys.stderr)
                time.sleep(3)

    run_seen, papers = set(), []
    for entry in entries:
        title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
        link = entry.get("link", "")
        aid = arxiv_id(link)
        if aid in run_seen or aid in seen["arxiv"]:
            continue  # de-dup within run AND never repeat a published paper
        run_seen.add(aid)
        summary = entry.get("summary", "")
        # RSS abstracts are prefixed with "...Announce Type: new\nAbstract: ..."
        if "Abstract:" in summary:
            summary = summary.split("Abstract:", 1)[1]
        summary = re.sub(r"\s+", " ", summary).strip()
        text = f"{title} {summary}"
        if cfg.get("english_only", True) and not is_english(text):
            continue
        themes = matched_themes(text, cfg)
        if not themes:
            continue
        authors = entry.get("author", "") or ", ".join(
            a.get("name", "") for a in entry.get("authors", [])[:4]
        )
        score = sum(ARXIV_THEME_WEIGHT.get(t, 1) for t in themes)
        papers.append(
            {
                "id": aid,
                "title": title,
                "url": link,
                "summary": summary[:320] + ("…" if len(summary) > 320 else ""),
                "authors": authors,
                "themes": sorted(themes),
                "score": score,
            }
        )
    papers.sort(key=lambda p: (p["score"], len(p["themes"])), reverse=True)
    print(f"[arxiv] {len(papers)} new matched papers (after dedup)", file=sys.stderr)
    return papers[: cfg["limits"]["arxiv"]]


# ------------------------------------------------------------------ site ----
DOCS = os.path.join(ROOT, "docs")

THEME_LABELS = {
    "ai_advancement": "AI advancement",
    "ai_agents": "AI agents",
    "mcp": "MCP",
    "frontier_labs": "Frontier labs",
    "faang": "FAANG",
}

PAGE_CSS = """
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
--muted:#8b949e;--link:#58a6ff;--chip:#1f6feb33;--chip-text:#79c0ff;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
line-height:1.5;}
.wrap{max-width:820px;margin:0 auto;padding:28px 18px 60px;}
header h1{font-size:26px;margin:0 0 4px;}
header .sub{color:var(--muted);font-size:14px;}
h2{font-size:18px;margin:34px 0 10px;padding-bottom:6px;
border-bottom:1px solid var(--border);}
.item{background:var(--card);border:1px solid var(--border);border-radius:10px;
padding:14px 16px;margin:10px 0;}
.item a.title{font-size:16px;font-weight:600;color:var(--link);
text-decoration:none;}
.item a.title:hover{text-decoration:underline;}
.desc{color:#c9d1d9;font-size:14px;margin:5px 0;}
.meta{color:var(--muted);font-size:12px;}
.chip{background:var(--chip);color:var(--chip-text);border-radius:5px;
padding:1px 7px;font-size:11px;margin-right:4px;white-space:nowrap;}
.empty{color:var(--muted);}
nav.bar{display:flex;justify-content:space-between;align-items:center;
flex-wrap:wrap;gap:10px;margin-bottom:18px;}
nav.bar a{color:var(--link);text-decoration:none;font-size:14px;}
.archive{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px;}
.archive a{background:var(--card);border:1px solid var(--border);
border-radius:8px;padding:6px 11px;color:var(--link);text-decoration:none;
font-size:13px;}
.archive a:hover{border-color:var(--link);}
footer{margin-top:40px;color:var(--muted);font-size:12px;
border-top:1px solid var(--border);padding-top:14px;}
"""


def esc(s):
    return html.escape(s or "")


def chips(themes):
    return " ".join(
        f'<span class="chip">{esc(THEME_LABELS.get(t, t))}</span>' for t in themes
    )


def render_items(repos, papers):
    out = ["<h2>📦 Trending GitHub repos</h2>"]
    if repos:
        for r in repos:
            meta = " · ".join(x for x in [r.get("lang"), r.get("stars")] if x)
            out.append(
                f'<div class="item">'
                f'<a class="title" href="{esc(r["url"])}" target="_blank" '
                f'rel="noopener">{esc(r["full_name"])}</a>'
                f'<div class="desc">{esc(r["desc"])}</div>'
                f'<div class="meta">{esc(meta)} &nbsp; {chips(r["themes"])}</div>'
                f"</div>"
            )
    else:
        out.append('<p class="empty">No matching repos today.</p>')

    out.append("<h2>📄 arXiv papers</h2>")
    if papers:
        for p in papers:
            out.append(
                f'<div class="item">'
                f'<a class="title" href="{esc(p["url"])}" target="_blank" '
                f'rel="noopener">{esc(p["title"])}</a>'
                f'<div class="meta">{esc(p["authors"])}</div>'
                f'<div class="desc">{esc(p["summary"])}</div>'
                f'<div>{chips(p["themes"])}</div>'
                f"</div>"
            )
    else:
        out.append('<p class="empty">No matching papers today.</p>')
    return "\n".join(out)


def render_page(date, repos, papers, archive_dates, is_index):
    nav = (
        '<nav class="bar"><a href="index.html">← Latest</a>'
        '<a href="https://github.com/anmolsam/ai-trendradar" target="_blank" '
        'rel="noopener">Source on GitHub</a></nav>'
        if not is_index
        else '<nav class="bar"><span></span>'
        '<a href="https://github.com/anmolsam/ai-trendradar" target="_blank" '
        'rel="noopener">Source on GitHub</a></nav>'
    )

    archive_html = ""
    if is_index and archive_dates:
        links = "".join(f'<a href="{d}.html">{d}</a>' for d in archive_dates)
        archive_html = f'<h2>🗓️ Archive</h2><div class="archive">{links}</div>'

    title = "AI TrendRadar" if is_index else f"AI TrendRadar — {date}"
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>{PAGE_CSS}</style>
</head><body><div class="wrap">
{nav}
<header>
<h1>🛰️ AI TrendRadar</h1>
<div class="sub">{esc(date)} · {len(repos)} repos · {len(papers)} papers ·
AI advancement, agents, MCP, frontier labs, FAANG</div>
</header>
{render_items(repos, papers)}
{archive_html}
<footer>Updated daily via GitHub Actions. Edit <code>config.yaml</code> to tune topics.</footer>
</div></body></html>"""


def list_archive_dates():
    """All dated pages already in docs/, newest first."""
    dates = []
    for path in glob.glob(os.path.join(DOCS, "*.html")):
        name = os.path.splitext(os.path.basename(path))[0]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            dates.append(name)
    return sorted(set(dates), reverse=True)


def write_site(repos, papers, today):
    os.makedirs(DOCS, exist_ok=True)
    # disable Jekyll so files are served verbatim
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    # today's archived page (overwrite if re-run same day)
    day_path = os.path.join(DOCS, f"{today}.html")
    with open(day_path, "w", encoding="utf-8") as f:
        f.write(render_page(today, repos, papers, [], is_index=False))

    # index = today's content + archive list of every past day
    archive = [d for d in list_archive_dates() if d != today]
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_page(today, repos, papers, archive, is_index=True))

    print(f"[site] wrote {day_path} and index.html "
          f"({len(repos)} repos, {len(papers)} papers, "
          f"{len(archive)} archived days)", file=sys.stderr)


# ------------------------------------------------------------------ main ----
def main():
    cfg = load_config()
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    seen = load_seen()
    repos = collect_github(cfg, seen)
    papers = fetch_arxiv(cfg, seen)

    write_site(repos, papers, today)

    # Record everything we published today so it never repeats on a future day.
    seen["github"].update(r["full_name"] for r in repos)
    seen["arxiv"].update(p["id"] for p in papers)
    save_seen(seen)
    print(f"[seen] ledger now holds {len(seen['github'])} repos, "
          f"{len(seen['arxiv'])} papers", file=sys.stderr)


if __name__ == "__main__":
    main()
