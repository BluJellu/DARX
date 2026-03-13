"""
osu! DA+RX Score Showcase Generator
-------------------------------------
Generates a beautiful HTML page of your top DA+RX plays,
including beatmap background art fetched from the osu! CDN.

Usage:
  python rx_showcase.py                  # top 50, all statuses
  python rx_showcase.py --limit 100
  python rx_showcase.py --filter ranked
  python rx_showcase.py --filter ranked+loved
  python rx_showcase.py --output mypage.html

Output: rx_showcase.html (next to this script)
Requires: config.ini (for OAuth token to resolve beatmapset IDs)
"""

import os
import sys
import json
import sqlite3
import argparse
import configparser
import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(_SCRIPT_DIR, "rx_scores.db")
OUT_PATH    = os.path.join(_SCRIPT_DIR, "rx_showcase.html")
CACHE_PATH  = os.path.join(_SCRIPT_DIR, ".beatmapset_cache.json")
BONUS_PP    = 413.894

# ── Auth ──────────────────────────────────────────────────────────────────────
def load_credentials():
    cfg = configparser.ConfigParser()
    try:
        cfg.read(os.path.join(_SCRIPT_DIR, "config.ini"))
        cid     = cfg.get("osu", "client_id",     fallback="").strip()
        csecret = cfg.get("osu", "client_secret", fallback="").strip()
        if cid and csecret:
            return cid, csecret
    except Exception:
        pass
    cid     = input("Client ID:     ").strip()
    csecret = input("Client Secret: ").strip()
    return cid, csecret

def get_token(client_id, client_secret):
    r = requests.post("https://osu.ppy.sh/oauth/token", json={
        "client_id": client_id, "client_secret": client_secret,
        "grant_type": "client_credentials", "scope": "public"
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]

# ── DB ────────────────────────────────────────────────────────────────────────
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_scores(status_filter=None, limit=50):
    with db_connect() as conn:
        if status_filter:
            statuses = [s.strip() for s in status_filter.split("+")]
            ph = ",".join("?" * len(statuses))
            rows = conn.execute(f"""
                SELECT * FROM scores
                WHERE score_id IN (
                    SELECT score_id FROM scores s
                    WHERE pp = (SELECT MAX(pp) FROM scores WHERE beatmap_id = s.beatmap_id)
                )
                AND COALESCE(beatmap_status, 'unknown') IN ({ph})
                ORDER BY COALESCE(pp_rework, pp_current, pp) DESC
                LIMIT ?
            """, (*statuses, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM scores
                WHERE score_id IN (
                    SELECT score_id FROM scores s
                    WHERE pp = (SELECT MAX(pp) FROM scores WHERE beatmap_id = s.beatmap_id)
                )
                ORDER BY COALESCE(pp_rework, pp_current, pp) DESC
                LIMIT ?
            """, (limit,)).fetchall()
    return rows

# ── Beatmapset ID lookup (cached) ─────────────────────────────────────────────
def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}

def save_cache(cache):
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f)

def resolve_beatmapset_ids(beatmap_ids, token, cache):
    headers = {"Authorization": f"Bearer {token}"}
    missing = [bid for bid in beatmap_ids if str(bid) not in cache]
    if missing:
        print(f"  Fetching beatmapset IDs for {len(missing)} beatmap(s)...")
        # Batch up to 50 at a time
        for i in range(0, len(missing), 50):
            batch = missing[i:i+50]
            params = [("ids[]", bid) for bid in batch]
            try:
                r = requests.get("https://osu.ppy.sh/api/v2/beatmaps",
                                 headers=headers, params=params, timeout=15)
                r.raise_for_status()
                for bm in r.json().get("beatmaps", []):
                    cache[str(bm["id"])] = bm["beatmapset_id"]
            except Exception as e:
                print(f"  Warning: failed batch {i//50+1}: {e}")
        save_cache(cache)
    return cache

def cover_url(beatmapset_id):
    return f"https://assets.ppy.sh/beatmaps/{beatmapset_id}/covers/cover@2x.jpg"

# ── PP helpers ────────────────────────────────────────────────────────────────
def weighted_total(rows, col):
    vals = [r[col] for r in rows if r[col] is not None]
    return sum(v * (0.95 ** i) for i, v in enumerate(vals))

# ── HTML generation ───────────────────────────────────────────────────────────
STATUS_COLOUR = {
    "ranked":    "#78e08f",
    "approved":  "#78e08f",
    "loved":     "#ff79c6",
    "qualified": "#f9ca24",
    "graveyard": "#636e72",
    "pending":   "#636e72",
    "wip":       "#636e72",
}

def build_html(rows, cache, status_filter, limit):
    has_rework = any(r["pp_rework"] is not None for r in rows)
    pp_current = weighted_total(rows, "pp_current") + BONUS_PP
    pp_rework  = (weighted_total(rows, "pp_rework") + BONUS_PP) if has_rework else None

    cards_html = ""
    for i, row in enumerate(rows, 1):
        bset_id    = cache.get(str(row["beatmap_id"]))
        bg_url     = cover_url(bset_id) if bset_id else ""
        bg_style   = f'style="background-image:url({bg_url})"' if bg_url else ""
        acc        = (row["accuracy"] or 0) * 100
        pp_val     = round(row["pp_current"] or row["pp"] or 0)
        stars      = row["stars"] or 0
        combo      = row["combo"] or 0
        bstatus    = row["beatmap_status"] or "unknown"
        st_colour  = STATUS_COLOUR.get(bstatus, "#aaa")
        mods       = row["mods_display"] or ""
        title      = row["title"] or "Unknown"
        diff       = row["diff_name"] or ""
        misses     = row["misses"] or 0
        n100       = row["n100"] or 0
        n50        = row["n50"] or 0

        # accuracy colour
        if acc >= 99.5:
            acc_cls = "acc-ss"
        elif acc >= 99:
            acc_cls = "acc-s"
        elif acc >= 97:
            acc_cls = "acc-a"
        else:
            acc_cls = "acc-b"

        # rank medal
        if i == 1:
            rank_cls = "rank-gold"
        elif i == 2:
            rank_cls = "rank-silver"
        elif i == 3:
            rank_cls = "rank-bronze"
        else:
            rank_cls = ""

        # hit stats string
        hit_parts = []
        if misses:  hit_parts.append(f'<span class="stat-miss">{misses}✗</span>')
        if n100:    hit_parts.append(f'<span class="stat-100">{n100}×100</span>')
        if n50:     hit_parts.append(f'<span class="stat-50">{n50}×50</span>')
        hits_html = " ".join(hit_parts) if hit_parts else '<span class="stat-fc">FC</span>'

        # rework column
        rework_html = ""
        if has_rework:
            pp_r = row["pp_rework"]
            rework_html = f'<div class="pp-rework">{round(pp_r)}pp <span class="pp-label">rework</span></div>' if pp_r else ""

        cards_html += f"""
        <div class="score-card" style="animation-delay:{(i-1)*0.04:.2f}s">
            <div class="score-bg" {bg_style}></div>
            <div class="score-bg-overlay"></div>
            <div class="score-rank {rank_cls}">{i}</div>
            <div class="score-body">
                <div class="score-top">
                    <div class="score-title-group">
                        <div class="score-title">{title}</div>
                        <div class="score-diff">{diff}</div>
                    </div>
                    <div class="score-pp-group">
                        <div class="score-pp">{pp_val}<span class="pp-unit">pp</span></div>
                        {rework_html}
                    </div>
                </div>
                <div class="score-bottom">
                    <span class="score-stars">★ {stars:.2f}</span>
                    <span class="score-acc {acc_cls}">{acc:.2f}%</span>
                    <span class="score-combo">{combo:,}x</span>
                    <span class="score-hits">{hits_html}</span>
                    <span class="score-mods">{mods}</span>
                    <span class="score-status" style="color:{st_colour}">{bstatus}</span>
                </div>
            </div>
        </div>"""

    filt_label = status_filter.upper() if status_filter else "ALL"
    pp_rework_html = f'<div class="header-stat"><span class="hs-label">rework</span><span class="hs-val rework">{round(pp_rework):,}pp</span></div>' if pp_rework else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DA+RX Top Plays</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
    --bg:        #08080f;
    --surface:   #0f0f1a;
    --border:    rgba(255,255,255,0.07);
    --pink:      #ff66aa;
    --pink-dim:  rgba(255,102,170,0.15);
    --yellow:    #ffd166;
    --cyan:      #67e8f9;
    --green:     #78e08f;
    --grey:      #4a4a6a;
    --text:      #e0e0f0;
    --text-dim:  #6b6b8f;
}}

html {{ scroll-behavior: smooth; }}

body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    font-size: 14px;
    min-height: 100vh;
    overflow-x: hidden;
}}

/* ── Background noise texture ── */
body::before {{
    content: '';
    position: fixed;
    inset: 0;
    background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)' opacity='0.04'/%3E%3C/svg%3E");
    pointer-events: none;
    z-index: 0;
    opacity: 0.6;
}}

/* ── Glowing orb behind header ── */
body::after {{
    content: '';
    position: fixed;
    top: -200px;
    left: 50%;
    transform: translateX(-50%);
    width: 800px;
    height: 600px;
    background: radial-gradient(ellipse, rgba(255,102,170,0.08) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
}}

.page-wrap {{
    position: relative;
    z-index: 1;
    max-width: 960px;
    margin: 0 auto;
    padding: 60px 24px 80px;
}}

/* ── Header ── */
.header {{
    text-align: center;
    margin-bottom: 56px;
}}

.header-eyebrow {{
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.25em;
    text-transform: uppercase;
    color: var(--pink);
    margin-bottom: 16px;
    opacity: 0;
    animation: fadeUp 0.6s ease forwards;
}}

.header-title {{
    font-family: 'Syne', sans-serif;
    font-size: clamp(36px, 6vw, 64px);
    font-weight: 800;
    line-height: 1.05;
    letter-spacing: -0.02em;
    background: linear-gradient(135deg, #fff 30%, var(--pink) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin-bottom: 32px;
    opacity: 0;
    animation: fadeUp 0.6s 0.1s ease forwards;
}}

.header-stats {{
    display: flex;
    justify-content: center;
    gap: 40px;
    opacity: 0;
    animation: fadeUp 0.6s 0.2s ease forwards;
}}

.header-stat {{
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
}}

.hs-label {{
    font-family: 'Space Mono', monospace;
    font-size: 10px;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--text-dim);
}}

.hs-val {{
    font-family: 'Space Mono', monospace;
    font-size: 22px;
    font-weight: 700;
    color: var(--yellow);
}}

.hs-val.rework {{ color: var(--pink); }}
.hs-val.count  {{ color: var(--text-dim); font-size: 16px; }}

.header-divider {{
    width: 1px;
    height: 40px;
    background: var(--border);
    align-self: center;
}}

/* ── Filter pill ── */
.filter-pill {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: var(--pink-dim);
    border: 1px solid rgba(255,102,170,0.3);
    border-radius: 100px;
    padding: 4px 14px;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.1em;
    color: var(--pink);
    text-transform: uppercase;
    margin-bottom: 28px;
    opacity: 0;
    animation: fadeUp 0.6s 0.25s ease forwards;
}}

/* ── Score cards ── */
.scores-list {{
    display: flex;
    flex-direction: column;
    gap: 10px;
}}

.score-card {{
    position: relative;
    display: flex;
    align-items: stretch;
    border-radius: 12px;
    border: 1px solid var(--border);
    overflow: hidden;
    background: var(--surface);
    transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
    opacity: 0;
    animation: fadeUp 0.5s ease forwards;
    min-height: 72px;
}}

.score-card:hover {{
    transform: translateX(4px);
    border-color: rgba(255,102,170,0.25);
    box-shadow: 0 8px 32px rgba(0,0,0,0.4), -4px 0 0 var(--pink);
}}

/* beatmap background strip */
.score-bg {{
    position: absolute;
    left: 0;
    top: 0;
    width: 220px;
    height: 100%;
    background-size: cover;
    background-position: center;
    background-color: #1a1a2e;
    flex-shrink: 0;
}}

.score-bg-overlay {{
    position: absolute;
    left: 0;
    top: 0;
    width: 220px;
    height: 100%;
    background: linear-gradient(
        to right,
        rgba(8,8,15,0.3) 0%,
        rgba(8,8,15,0.85) 70%,
        rgba(15,15,26,1) 100%
    );
}}

/* rank number */
.score-rank {{
    position: absolute;
    left: 0;
    top: 0;
    width: 220px;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: 'Syne', sans-serif;
    font-size: 38px;
    font-weight: 800;
    color: rgba(255,255,255,0.18);
    letter-spacing: -0.03em;
    pointer-events: none;
    user-select: none;
}}

.score-rank.rank-gold   {{ color: rgba(255,209,102,0.45); text-shadow: 0 0 40px rgba(255,209,102,0.3); }}
.score-rank.rank-silver {{ color: rgba(200,200,220,0.4);  text-shadow: 0 0 30px rgba(200,200,220,0.2); }}
.score-rank.rank-bronze {{ color: rgba(205,140,100,0.4);  text-shadow: 0 0 30px rgba(205,140,100,0.2); }}

/* card content */
.score-body {{
    margin-left: 220px;
    flex: 1;
    padding: 14px 18px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
}}

.score-top {{
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
}}

.score-title-group {{
    min-width: 0;
    flex: 1;
}}

.score-title {{
    font-family: 'Syne', sans-serif;
    font-weight: 600;
    font-size: 15px;
    color: #fff;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    line-height: 1.2;
}}

.score-diff {{
    font-size: 12px;
    color: var(--text-dim);
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}}

.score-pp-group {{
    text-align: right;
    flex-shrink: 0;
}}

.score-pp {{
    font-family: 'Space Mono', monospace;
    font-size: 20px;
    font-weight: 700;
    color: var(--yellow);
    line-height: 1;
}}

.pp-unit {{
    font-size: 12px;
    font-weight: 400;
    color: var(--text-dim);
    margin-left: 2px;
}}

.pp-rework {{
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    color: var(--pink);
    margin-top: 3px;
    text-align: right;
}}

.pp-label {{
    font-size: 10px;
    color: var(--text-dim);
    letter-spacing: 0.05em;
}}

/* bottom row */
.score-bottom {{
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
}}

.score-stars  {{ color: #ffd166; }}
.score-combo  {{ color: var(--text-dim); }}
.score-mods   {{ color: var(--cyan); font-size: 10px; max-width: 220px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.score-status {{ font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase; }}

.score-acc        {{ font-weight: 700; }}
.acc-ss           {{ color: #ffeaa7; text-shadow: 0 0 12px rgba(255,234,167,0.4); }}
.acc-s            {{ color: var(--green); }}
.acc-a            {{ color: var(--cyan); }}
.acc-b            {{ color: var(--text-dim); }}

.score-hits       {{ display: flex; gap: 6px; align-items: center; }}
.stat-miss        {{ color: #ff7675; }}
.stat-100         {{ color: #74b9ff; }}
.stat-50          {{ color: #fd79a8; }}
.stat-fc          {{ color: var(--green); }}

/* ── Footer ── */
.footer {{
    text-align: center;
    margin-top: 64px;
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    color: var(--grey);
    letter-spacing: 0.05em;
    opacity: 0;
    animation: fadeUp 0.6s 0.3s ease forwards;
}}

.footer a {{
    color: var(--pink);
    text-decoration: none;
}}

/* ── Animations ── */
@keyframes fadeUp {{
    from {{ opacity: 0; transform: translateY(14px); }}
    to   {{ opacity: 1; transform: translateY(0);    }}
}}

/* ── Responsive ── */
@media (max-width: 600px) {{
    .score-bg, .score-bg-overlay, .score-rank {{ width: 110px; }}
    .score-body {{ margin-left: 110px; }}
    .score-mods {{ display: none; }}
    .header-stats {{ gap: 20px; flex-wrap: wrap; }}
    .header-divider {{ display: none; }}
}}
</style>
</head>
<body>
<div class="page-wrap">

    <div class="header">
        <div class="header-eyebrow">DA+RX top plays</div>
        <h1 class="header-title">My Best Scores</h1>
        <div class="header-stats">
            <div class="header-stat">
                <span class="hs-label">total pp</span>
                <span class="hs-val">{round(pp_current):,}pp</span>
            </div>
            {f'<div class="header-divider"></div>' if pp_rework_html else ''}
            {pp_rework_html}
            <div class="header-divider"></div>
            <div class="header-stat">
                <span class="hs-label">maps</span>
                <span class="hs-val count">{len(rows)}</span>
            </div>
        </div>
    </div>

    {'<div style="text-align:center"><div class="filter-pill">● ' + filt_label + '</div></div>' if filt_label != 'ALL' else ''}

    <div class="scores-list">
{cards_html}
    </div>

    <div class="footer">
        generated by rx_showcase.py &nbsp;·&nbsp;
        <a href="https://osu.ppy.sh">osu.ppy.sh</a>
    </div>

</div>
</body>
</html>"""

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--filter",  default=None,  help="e.g. ranked  or  ranked+loved")
    parser.add_argument("--limit",   default=50,    type=int, help="Max scores to show (default 50)")
    parser.add_argument("--output",  default=OUT_PATH)
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    print("Loading scores from database...")
    rows = fetch_scores(args.filter, args.limit)
    if not rows:
        print("No scores found.")
        sys.exit(0)
    print(f"  {len(rows)} score(s) loaded")

    print("Authenticating with osu! API...")
    cid, csecret = load_credentials()
    try:
        token = get_token(cid, csecret)
    except Exception as e:
        print(f"Auth failed: {e}")
        sys.exit(1)

    cache = load_cache()
    beatmap_ids = list({r["beatmap_id"] for r in rows})
    cache = resolve_beatmapset_ids(beatmap_ids, token, cache)

    print("Generating HTML...")
    html = build_html(rows, cache, args.filter, args.limit)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Done! Open in your browser:")
    print(f"  {os.path.abspath(args.output)}")

if __name__ == "__main__":
    main()