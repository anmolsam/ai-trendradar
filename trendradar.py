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
        "feeds": set(data.get("feeds", [])),
    }


def save_seen(seen):
    os.makedirs(os.path.dirname(SEEN_PATH), exist_ok=True)
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {
                "github": sorted(seen["github"]),
                "arxiv": sorted(seen["arxiv"]),
                "feeds": sorted(seen["feeds"]),
            },
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

    gh = cfg.get("github", {})
    min_stars = gh.get("min_stars", 40)
    since = (
        dt.datetime.now(dt.timezone.utc)
        - dt.timedelta(days=gh.get("active_within_days", 30))
    ).strftime("%Y-%m-%d")

    for query in cfg.get("github_search_queries", []):
        # popular + recently-maintained + on-topic — not just brand-new 0-star repos
        q = f"{query} stars:>{min_stars} pushed:>{since}"
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
    min_stars = cfg.get("github", {}).get("min_stars", 40)
    picked = {}
    for repo in fetch_github_trending(cfg) + fetch_github_search(cfg):
        key = repo["full_name"]
        if key in seen["github"] or key in picked:
            continue  # never repeat a repo we've published before
        # Quality bar: must be gaining stars today OR already well-starred.
        if repo["velocity"] == 0 and repo["stars_total"] < min_stars:
            continue
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
        if score < cfg.get("arxiv_min_score", 2):
            continue  # drop weak / generic matches
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


# ----------------------------------------------------- hugging face papers --
def fetch_hf_papers(cfg, seen):
    """Hugging Face Daily Papers — ranked by community upvotes (best signal)."""
    if not cfg.get("huggingface", {}).get("enabled", True):
        return []
    try:
        r = requests.get(
            "https://huggingface.co/api/daily_papers?limit=100", headers=UA, timeout=30
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[hf] fetch failed: {e}", file=sys.stderr)
        return []

    # Rolling window — HF papers mature (gain upvotes) over ~2 days. The ledger
    # then ensures none repeat, so each day still shows only fresh-to-you papers.
    look = cfg.get("huggingface", {}).get("lookback_days", 3)
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=look)).strftime(
        "%Y-%m-%d"
    )

    out = []
    for row in data:
        if row.get("publishedAt", "")[:10] < cutoff:
            continue  # older than the window
        p = row.get("paper", {})
        aid = p.get("id", "")
        if not aid or aid in seen["arxiv"]:
            continue  # shares arXiv IDs — never repeat across HF/arXiv or days
        title = re.sub(r"\s+", " ", p.get("title", "")).strip()
        summary = re.sub(r"\s+", " ", (p.get("ai_summary") or p.get("summary") or "")).strip()
        keywords = " ".join(p.get("ai_keywords", []) or [])
        text = f"{title} {summary} {keywords}"
        if cfg.get("english_only", True) and not is_english(text):
            continue
        themes = matched_themes(text, cfg)
        if not themes:
            continue
        upvotes = p.get("upvotes", 0) or 0
        if upvotes < cfg.get("huggingface", {}).get("min_upvotes", 3):
            continue  # only community-validated papers
        authors = ", ".join(
            a.get("name", "") for a in (p.get("authors") or [])[:4]
        )
        out.append(
            {
                "id": aid,
                "title": title,
                "url": f"https://huggingface.co/papers/{aid}",
                "summary": summary[:300] + ("…" if len(summary) > 300 else ""),
                "authors": authors,
                "themes": sorted(themes),
                "upvotes": p.get("upvotes", 0) or 0,
                "gh_repo": p.get("githubRepo", "") or "",
                "gh_stars": p.get("githubStars", 0) or 0,
            }
        )
    out.sort(key=lambda p: (p["upvotes"], len(p["themes"])), reverse=True)
    print(f"[hf] {len(out)} new upvoted papers since {cutoff} (after dedup)",
          file=sys.stderr)
    return out[: cfg["limits"]["huggingface"]]


# ------------------------------------------------- lab / institute feeds ----
def fetch_feeds(cfg, seen):
    """Pull each lab/institute blog RSS; theme + English + recency filtered."""
    feeds = cfg.get("feeds", [])
    if not feeds:
        return []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        days=cfg.get("feeds_lookback_days", 7)
    )
    run_seen, items = set(), []
    for feed in feeds:
        url, label, ftype = feed["url"], feed["label"], feed.get("type", "lab")
        try:
            resp = requests.get(url, headers=UA, timeout=20)
            resp.raise_for_status()
            entries = feedparser.parse(resp.content).entries
        except Exception as e:
            print(f"[feeds] {label} failed: {e}", file=sys.stderr)
            continue

        for entry in entries[:40]:
            link = entry.get("link", "")
            if not link or link in seen["feeds"] or link in run_seen:
                continue
            pub = entry.get("published_parsed") or entry.get("updated_parsed")
            if not pub:
                continue
            pub_dt = dt.datetime(*pub[:6], tzinfo=dt.timezone.utc)
            if pub_dt < cutoff:
                continue
            title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
            raw = entry.get("summary", "") or ""
            summary = re.sub(r"<[^>]+>", " ", raw)
            summary = re.sub(r"\s+", " ", html.unescape(summary)).strip()
            text = f"{title} {summary}"
            if cfg.get("english_only", True) and not is_english(text):
                continue
            themes = matched_themes(text, cfg)
            if not themes:
                continue
            run_seen.add(link)
            items.append(
                {
                    "title": title,
                    "url": link,
                    "summary": summary[:280] + ("…" if len(summary) > 280 else ""),
                    "source": label,
                    "type": ftype,
                    "themes": sorted(themes),
                    "ts": pub_dt.timestamp(),
                    "date": pub_dt.strftime("%Y-%m-%d"),
                }
            )
    # Own-blog labs/institutes rank above third-party "news"; recent first within.
    type_rank = {"lab": 0, "institute": 0, "news": 1}
    items.sort(key=lambda x: (type_rank.get(x["type"], 1), -x["ts"]))

    # Cap per source so one noisy feed (e.g. a Google-News query) can't dominate.
    per_source = cfg.get("feeds_per_source", 3)
    counts, capped = {}, []
    for it in items:
        if counts.get(it["source"], 0) >= per_source:
            continue
        counts[it["source"]] = counts.get(it["source"], 0) + 1
        capped.append(it)
    print(f"[feeds] {len(items)} new posts from {len(feeds)} feeds → "
          f"{len(capped)} after per-source cap", file=sys.stderr)
    return capped[: cfg["limits"]["feeds"]]


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
.badge{float:right;background:#238636;color:#fff;border-radius:5px;
padding:1px 8px;font-size:12px;font-weight:600;margin-left:8px;}
.src{display:inline-block;background:#30363d;color:#adbac7;border-radius:5px;
padding:1px 8px;font-size:11px;font-weight:600;margin-right:6px;}
.empty{color:var(--muted);}
.lead{color:var(--muted);font-size:13px;margin:2px 0 0;}
.rank{display:inline-block;min-width:22px;color:var(--muted);font-weight:700;
font-size:13px;margin-right:6px;}
.tabs{position:sticky;top:0;z-index:5;background:var(--bg);display:flex;
flex-wrap:wrap;gap:8px;padding:12px 0;margin:6px 0 4px;
border-bottom:1px solid var(--border);}
.tab{background:var(--card);border:1px solid var(--border);color:var(--text);
border-radius:20px;padding:7px 16px;font-size:14px;font-weight:600;cursor:pointer;}
.tab:hover{border-color:var(--link);}
.tab.active{background:var(--link);color:#04101f;border-color:var(--link);}
.hero{background:linear-gradient(135deg,#1f6feb22,#161b22);
border:1px solid #1f6feb55;border-radius:12px;padding:13px 16px;margin:10px 0;}
.hero .tag{font-size:11px;font-weight:700;color:#ffa657;letter-spacing:.04em;
text-transform:uppercase;}
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


def card(title, url, lead="", body="", themes=(), badge="", src="", rank=0):
    badge_html = f'<span class="badge">{esc(badge)}</span>' if badge else ""
    src_html = f'<span class="src">{esc(src)}</span>' if src else ""
    rank_html = f'<span class="rank">#{rank}</span>' if rank else ""
    lead_html = f'<div class="lead">{esc(lead)}</div>' if lead else ""
    body_html = f'<div class="desc">{esc(body)}</div>' if body else ""
    return (
        f'<div class="item">{badge_html}{rank_html}'
        f'{src_html}<a class="title" href="{esc(url)}" target="_blank" '
        f'rel="noopener">{esc(title)}</a>'
        f"{lead_html}{body_html}"
        f'<div class="meta">{chips(themes)}</div></div>'
    )


def render_section(cat, heading, cards, empty_msg):
    body = "\n".join(cards) if cards else f'<p class="empty">{empty_msg}</p>'
    return f'<section data-cat="{cat}"><h2>{heading}</h2>\n{body}</section>'


# Clickable filter tabs + client-side category toggle. Static-page friendly.
TABS_HTML = """<div class="tabs">
<button class="tab active" data-tab="all" onclick="flt('all',this)">All</button>
<button class="tab" data-tab="news" onclick="flt('news',this)">📰 News</button>
<button class="tab" data-tab="papers" onclick="flt('papers',this)">📄 Papers</button>
<button class="tab" data-tab="repos" onclick="flt('repos',this)">📦 Repos</button>
</div>"""

FILTER_JS = """<script>
function flt(cat,btn){
  document.querySelectorAll('[data-cat]').forEach(function(el){
    el.style.display=(cat==='all'||el.dataset.cat===cat)?'':'none';
  });
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active');});
  btn.classList.add('active');
}
</script>"""


def hero(tag, cat, inner):
    return f'<div class="hero" data-cat="{cat}"><div class="tag">{tag}</div>{inner}</div>'


def render_highlights(data):
    """🔥 The single most-famous item in each category, pinned at the top."""
    cards = []
    if data["repos"]:
        r = data["repos"][0]
        meta = " · ".join(x for x in [r.get("lang"), r.get("stars")] if x)
        cards.append(hero("🔥 Hottest repo today", "repos",
            card(r["full_name"], r["url"], lead=meta, body=r["desc"],
                 themes=r["themes"])))
    if data["hf"]:
        p = data["hf"][0]
        cards.append(hero(f"🔥 Top paper today · ▲{p['upvotes']} upvotes", "papers",
            card(p["title"], p["url"], lead=p["authors"], body=p["summary"],
                 themes=p["themes"])))
    if data["feeds"]:
        f = data["feeds"][0]
        cards.append(hero(f"🔥 Top news today · {f['source']}", "news",
            card(f["title"], f["url"], lead=f.get("date", ""), body=f["summary"],
                 themes=f["themes"])))
    if not cards:
        return ""
    return '<h2>🔥 Most famous today</h2>\n' + "\n".join(cards)


def render_body(data):
    parts = [render_highlights(data)]

    feed_cards = [
        card(f["title"], f["url"], lead=f.get("date", ""), body=f["summary"],
             themes=f["themes"], src=f["source"], rank=i)
        for i, f in enumerate(data["feeds"], 1)
    ]
    parts.append(render_section("news",
        "🏢 From the top labs &amp; institutes", feed_cards,
        "No new lab/institute posts in the window."))

    hf_cards = []
    for i, p in enumerate(data["hf"], 1):
        extra = f" · {p['gh_stars']:,}⭐ repo" if p.get("gh_stars") else ""
        hf_cards.append(card(
            p["title"], p["url"], lead=(p["authors"] + extra) if p["authors"] else extra,
            body=p["summary"], themes=p["themes"], badge=f"▲ {p['upvotes']}", rank=i))
    parts.append(render_section("papers",
        "🤗 Hugging Face — most-upvoted papers", hf_cards,
        "No new HF papers today."))

    repo_cards = [
        card(r["full_name"], r["url"],
             lead=" · ".join(x for x in [r.get("lang"), r.get("stars")] if x),
             body=r["desc"], themes=r["themes"], rank=i)
        for i, r in enumerate(data["repos"], 1)
    ]
    parts.append(render_section("repos",
        "📦 Trending GitHub repos", repo_cards, "No matching repos today."))

    paper_cards = [
        card(p["title"], p["url"], lead=p["authors"], body=p["summary"],
             themes=p["themes"], rank=i)
        for i, p in enumerate(data["arxiv"], 1)
    ]
    parts.append(render_section("papers",
        "📄 Fresh arXiv papers", paper_cards, "No matching papers today."))

    return "\n".join(parts)


def render_page(date, data):
    # TODAY ONLY — no archive, no history. index.html is fully replaced each day.
    nav = ('<nav class="bar"><span></span>'
           '<a href="https://github.com/anmolsam/ai-trendradar" target="_blank" '
           'rel="noopener">Source on GitHub</a></nav>')
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="3600">
<title>AI TrendRadar — Today</title>
<style>{PAGE_CSS}</style>
</head><body><div class="wrap">
{nav}
<header>
<h1>🛰️ AI TrendRadar — Today</h1>
<div class="sub">{esc(date)} · {len(data["feeds"])} lab posts ·
{len(data["hf"])} HF papers · {len(data["repos"])} repos ·
{len(data["arxiv"])} arXiv · AI advancement, agents, MCP, frontier labs, FAANG</div>
</header>
{TABS_HTML}
{render_body(data)}
<footer>Fresh every day at 09:00 IST via GitHub Actions — today only, never repeated.
Edit <code>config.yaml</code> to tune topics.</footer>
</div>
{FILTER_JS}
</body></html>"""


def write_site(data, today):
    os.makedirs(DOCS, exist_ok=True)
    open(os.path.join(DOCS, ".nojekyll"), "w").close()  # serve files verbatim

    # Remove any leftover dated archive pages — we keep today only.
    for old in glob.glob(os.path.join(DOCS, "[0-9]" * 4 + "-*.html")):
        os.remove(old)

    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(render_page(today, data))

    print(f"[site] wrote index.html (today only) — "
          f"{len(data['feeds'])} posts, {len(data['hf'])} hf, "
          f"{len(data['repos'])} repos, {len(data['arxiv'])} arxiv",
          file=sys.stderr)


# ------------------------------------------------------------------ main ----
def main():
    cfg = load_config()
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    seen = load_seen()
    # HF first; its arXiv IDs get reserved so the arXiv section won't duplicate them.
    hf = fetch_hf_papers(cfg, seen)
    seen["arxiv"].update(p["id"] for p in hf)

    repos = collect_github(cfg, seen)
    arxiv = fetch_arxiv(cfg, seen)
    feeds = fetch_feeds(cfg, seen)

    data = {"feeds": feeds, "hf": hf, "repos": repos, "arxiv": arxiv}
    write_site(data, today)

    # Record everything published today so it never repeats on a future day.
    seen["github"].update(r["full_name"] for r in repos)
    seen["arxiv"].update(p["id"] for p in arxiv)
    seen["feeds"].update(f["url"] for f in feeds)
    save_seen(seen)
    print(f"[seen] ledger: {len(seen['github'])} repos, "
          f"{len(seen['arxiv'])} papers, {len(seen['feeds'])} posts",
          file=sys.stderr)


if __name__ == "__main__":
    main()
