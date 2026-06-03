#!/usr/bin/env python3
"""
AI TrendRadar — daily digest of trending GitHub repos + arXiv papers
on AI advancement, AI agents, MCP servers, frontier labs, and FAANG.

Runs in GitHub Actions, emails the digest via Gmail SMTP.
Config lives in config.yaml; secrets (GMAIL_USER, GMAIL_APP_PASSWORD) come from env.
"""

import os
import re
import sys
import html
import time
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

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
        star_el = row.select_one("span.d-inline-block.float-sm-right")
        if star_el:
            stars_today = star_el.get_text(strip=True)
        repos.append(
            {
                "full_name": full_name,
                "url": url,
                "desc": desc,
                "lang": lang,
                "stars": stars_today,
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
            repos.append(
                {
                    "full_name": it["full_name"],
                    "url": it["html_url"],
                    "desc": it.get("description") or "",
                    "lang": it.get("language") or "",
                    "stars": f"{it.get('stargazers_count', 0)} stars",
                    "source": "search",
                }
            )
    print(f"[github search] collected {len(repos)} repos", file=sys.stderr)
    return repos


def collect_github(cfg):
    seen = {}
    for repo in fetch_github_trending(cfg) + fetch_github_search(cfg):
        text = f"{repo['full_name']} {repo['desc']}"
        if cfg.get("english_only", True) and not is_english(text):
            continue
        themes = matched_themes(text, cfg)
        if not themes:
            continue
        key = repo["full_name"]
        if key in seen:
            continue
        repo["themes"] = sorted(themes)
        seen[key] = repo
    out = list(seen.values())
    # trending-scraped first, then by theme count
    out.sort(key=lambda r: (r["source"] != "trending", -len(r["themes"])))
    return out[: cfg["limits"]["github"]]


# ---------------------------------------------------------------- arxiv -----
def fetch_arxiv(cfg):
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

    seen, papers = set(), []
    for entry in entries:
        title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
        link = entry.get("link", "")
        if link in seen:
            continue
        seen.add(link)
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
        papers.append(
            {
                "title": title,
                "url": link,
                "summary": summary[:320] + ("…" if len(summary) > 320 else ""),
                "authors": authors,
                "themes": sorted(themes),
            }
        )
    papers.sort(key=lambda p: -len(p["themes"]))
    print(f"[arxiv] matched {len(papers)} papers", file=sys.stderr)
    return papers[: cfg["limits"]["arxiv"]]


# ----------------------------------------------------------------- email ----
THEME_LABELS = {
    "ai_advancement": "AI advancement",
    "ai_agents": "AI agents",
    "mcp": "MCP",
    "frontier_labs": "Frontier labs",
    "faang": "FAANG",
}


def chips(themes):
    return " ".join(
        f'<span style="background:#eef;border-radius:4px;padding:1px 6px;'
        f'font-size:11px;color:#334;">{THEME_LABELS.get(t, t)}</span>'
        for t in themes
    )


def build_html(repos, papers, today):
    def esc(s):
        return html.escape(s or "")

    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:720px;margin:auto;color:#1a1a1a;">',
        f'<h1 style="font-size:22px;">🛰️ AI TrendRadar — {today}</h1>',
        f'<p style="color:#666;">{len(repos)} repos · {len(papers)} papers · '
        "AI advancement, agents, MCP, frontier labs, FAANG</p>",
    ]

    parts.append('<h2 style="border-bottom:2px solid #333;">📦 Trending GitHub repos</h2>')
    if repos:
        for r in repos:
            meta = " · ".join(x for x in [r.get("lang"), r.get("stars")] if x)
            parts.append(
                f'<div style="margin:14px 0;">'
                f'<a href="{esc(r["url"])}" style="font-size:16px;font-weight:600;'
                f'color:#0366d6;text-decoration:none;">{esc(r["full_name"])}</a>'
                f'<div style="color:#555;font-size:14px;margin:2px 0;">{esc(r["desc"])}</div>'
                f'<div style="color:#888;font-size:12px;">{esc(meta)} &nbsp; {chips(r["themes"])}</div>'
                f"</div>"
            )
    else:
        parts.append('<p style="color:#999;">No matching repos today.</p>')

    parts.append('<h2 style="border-bottom:2px solid #333;margin-top:28px;">📄 arXiv papers</h2>')
    if papers:
        for p in papers:
            parts.append(
                f'<div style="margin:14px 0;">'
                f'<a href="{esc(p["url"])}" style="font-size:16px;font-weight:600;'
                f'color:#0366d6;text-decoration:none;">{esc(p["title"])}</a>'
                f'<div style="color:#777;font-size:12px;margin:2px 0;">{esc(p["authors"])}</div>'
                f'<div style="color:#555;font-size:14px;">{esc(p["summary"])}</div>'
                f'<div style="margin-top:3px;">{chips(p["themes"])}</div>'
                f"</div>"
            )
    else:
        parts.append('<p style="color:#999;">No matching papers today.</p>')

    parts.append(
        '<p style="color:#aaa;font-size:11px;margin-top:30px;">'
        "Generated by ai-trendradar. Edit config.yaml to tune topics.</p></div>"
    )
    return "\n".join(parts)


def send_email(cfg, html_body, today):
    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipients = cfg["email"]["to"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f'{cfg["email"]["subject_prefix"]} — {today}'
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText("HTML email — view in an HTML-capable client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, password)
        server.sendmail(user, recipients, msg.as_string())
    print(f"[email] sent to {recipients}", file=sys.stderr)


# ------------------------------------------------------------------ main ----
def main():
    cfg = load_config()
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    repos = collect_github(cfg)
    papers = fetch_arxiv(cfg)
    html_body = build_html(repos, papers, today)

    dry = "--dry-run" in sys.argv or not os.environ.get("GMAIL_USER")
    if dry:
        out = os.path.join(ROOT, "digest_preview.html")
        with open(out, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"[dry-run] wrote preview to {out} "
              f"({len(repos)} repos, {len(papers)} papers)", file=sys.stderr)
        return

    send_email(cfg, html_body, today)


if __name__ == "__main__":
    main()
