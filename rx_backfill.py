"""
osu! DA+RX Score Backfiller
----------------------------
Fetches your existing scores via the osu! API and loads any DA+RX
combinations into rx_scores.db (the same database rx_tracker.py uses).

Usage:
    python3 rx_backfill.py

It will scan:
  - /users/{id}/scores/best   (up to 100 scores, your top PP plays)
  - /users/{id}/scores/recent (up to 100 scores, most recent plays)

Scores already in the database are skipped.
"""

import os
import sys
import json
import subprocess
import configparser
import requests

# ── Shared config (must match rx_tracker.py) ─────────────────────────────────
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
_BRIDGE_DIR  = os.path.join(_SCRIPT_DIR, "osu_pp_bridge")
DB_PATH      = os.path.join(_SCRIPT_DIR, "rx_scores.db")
OSU_CACHE_DIR = os.path.expanduser("~/.cache/osu_lookup")

# ── Import shared helpers from rx_tracker ─────────────────────────────────────
sys.path.insert(0, _SCRIPT_DIR)
from rx_tracker import (
    db_init, db_insert_score, db_connect,
    is_da_rx, format_mods, get_mods_for_bridge, mods_without_rx,
    get_token, fetch_osu_file, calculate_pp,
    BRIDGE_AVAILABLE, BRIDGE_REWORK_AVAILABLE,
    _BRIDGE_DIR, _BRIDGE_REWORK_DIR,
    STATUS_NAMES,
    RESET, BOLD, PINK, CYAN, GREEN, YELLOW, RED, GREY, WHITE,
)

def clr(text, c): return f"{c}{text}{RESET}"

# ── API ───────────────────────────────────────────────────────────────────────
def fetch_user_scores(token, user_id, score_type, limit=100, offset=0):
    headers = {"Authorization": f"Bearer {token}", "x-api-version": "20220705"}
    params  = {"mode": "osu", "limit": limit, "offset": offset}
    resp = requests.get(
        f"https://osu.ppy.sh/api/v2/users/{user_id}/scores/{score_type}",
        headers=headers, params=params, timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

def fetch_score_by_id(token, score_id):
    headers = {"Authorization": f"Bearer {token}", "x-api-version": "20220705"}
    # Try the generic endpoint first, then ruleset-specific
    for url in [
        f"https://osu.ppy.sh/api/v2/scores/{score_id}",
        f"https://osu.ppy.sh/api/v2/scores/osu/{score_id}",
    ]:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            continue
    raise ValueError(f"Score {score_id} not found")

# ── Stat adjustment ──────────────────────────────────────────────────────────
def parse_adjustments(parts):
    """
    Parse adjustment tokens like: 100=2 miss=1 50=1 sliderend=3
    Returns dict mapping stat key → amount to subtract.
    Accepted keys (all subtract from that stat, add to 300s):
      100 / ok        → n100
      50  / meh       → n50
      miss / misses   → misses
      sliderend / se  → slider_tail_hit
      largetick / lt  → large_tick_hit
    """
    KEY_MAP = {
        "100": "ok", "ok": "ok",
        "50":  "meh", "meh": "meh",
        "miss": "miss", "misses": "miss",
        "sliderend": "sliderend", "se": "sliderend",
        "largetick": "largetick", "lt": "largetick",
    }
    adj = {}
    for part in parts:
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.lower().strip()
        if k not in KEY_MAP or not v.isdigit():
            print(f"  {YELLOW}  Unknown adjustment '{part}', skipping{RESET}")
            continue
        adj[KEY_MAP[k]] = int(v)
    return adj

def apply_adjustments(score, adj):
    """
    Return a copy of score with statistics adjusted.
    Subtracted counts are added back to 'great' (300s).
    """
    if not adj:
        return score
    import copy
    s = copy.deepcopy(score)
    stats = s.setdefault("statistics", {})

    STAT_KEYS = {
        "ok":         ["ok",    "count_100"],
        "meh":        ["meh",   "count_50"],
        "miss":       ["miss",  "count_miss"],
        "sliderend":  ["slider_tail_hit"],
        "largetick":  ["large_tick_hit"],
    }

    # se= and lt= add to counts (correcting false misses on slider ends / large ticks)
    # ok= meh= miss= subtract from that judgment and add back to 300s
    ADD_KEYS = ("sliderend", "largetick")

    total_subtracted = 0
    for adj_key, amount in adj.items():
        for stat_key in STAT_KEYS.get(adj_key, []):
            if stat_key in stats and stats[stat_key] is not None:
                if adj_key in ADD_KEYS:
                    stats[stat_key] = stats[stat_key] + amount
                else:
                    stats[stat_key] = max(0, stats[stat_key] - amount)
                    if adj_key in ("ok", "meh", "miss"):
                        total_subtracted += amount
                break

    # Add corrected hits back to 300s
    if total_subtracted > 0:
        for key in ("great", "count_300"):
            if key in stats and stats[key] is not None:
                stats[key] += total_subtracted
                break

    # Recalculate accuracy from adjusted stats
    n300 = stats.get("great") or stats.get("count_300") or 0
    n100 = stats.get("ok")    or stats.get("count_100") or 0
    n50  = stats.get("meh")   or stats.get("count_50")  or 0
    nm   = stats.get("miss")  or stats.get("count_miss") or 0
    total_hits = n300 + n100 + n50 + nm
    if total_hits > 0:
        s["accuracy"] = (n300 * 300 + n100 * 100 + n50 * 50) / (total_hits * 300)

    return s

# ── Score processing ──────────────────────────────────────────────────────────
def process_score(s, token=None, adj=None):
    """
    Validate, calculate PP, and save a single score dict to the DB.
    adj: optional dict from parse_adjustments() to correct bugged stats.
    Returns (saved: bool, message: str).
    """
    if adj:
        s = apply_adjustments(s, adj)
    score_id   = s.get("id")
    beatmap    = s.get("beatmap")    or {}
    beatmapset = s.get("beatmapset") or {}
    beatmap_id = beatmap.get("id")
    mods       = s.get("mods", [])
    title      = beatmapset.get("title", "?")
    diff       = beatmap.get("version", "?")
    bstatus    = STATUS_NAMES.get(beatmap.get("ranked"), None)
    mods_disp  = format_mods(mods)

    if not score_id or not beatmap_id:
        return False, "missing score_id or beatmap_id"

    if not is_da_rx(mods):
        return False, f"not DA+RX ({mods_disp})"

    with db_connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM scores WHERE score_id=?", (score_id,)
        ).fetchone()
    if exists and not adj:
        return False, f"already in database"

    try:
        osu_path       = fetch_osu_file(beatmap_id)
        pp_c, stars    = calculate_pp(s, osu_path, _BRIDGE_DIR)
        pp_r           = None
        pp_norx        = None
        if BRIDGE_REWORK_AVAILABLE:
            pp_r,  _   = calculate_pp(s, osu_path, _BRIDGE_REWORK_DIR)
            pp_norx, _ = calculate_pp(s, osu_path, _BRIDGE_REWORK_DIR,
                                      override_mods=mods_without_rx(mods))
    except Exception as e:
        return False, f"PP calc error: {e}"

    stats  = s.get("statistics", {})
    acc    = s.get("accuracy", 0.0)
    combo  = s.get("max_combo", 0)
    n300   = stats.get("great") or stats.get("count_300") or 0
    n100   = stats.get("ok")    or stats.get("count_100") or 0
    n50    = stats.get("meh")   or stats.get("count_50")  or 0
    nm     = stats.get("miss")  or stats.get("count_miss") or 0
    date   = (s.get("ended_at") or s.get("created_at") or "")[:19]

    if adj and exists:
        with db_connect() as conn:
            conn.execute("DELETE FROM scores WHERE score_id=?", (score_id,))
    db_insert_score(score_id, beatmap_id, title, diff, stars, pp_c,
                    acc, combo, n300, n100, n50, nm, mods_disp, date,
                    pp_current=pp_c, pp_rework=pp_r, pp_rework_norx=pp_norx,
                    beatmap_status=bstatus)

    parts = [f"{round(pp_c)}pp current"]
    if pp_r    is not None: parts.append(f"rework {round(pp_r)}pp")
    if pp_norx is not None: parts.append(f"no-RX {round(pp_norx)}pp")
    return True, f"✓  {' / '.join(parts)}  ★{stars:.2f}  —  {title} [{diff}]  {mods_disp}"

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"""
{PINK}{BOLD}  ╔════════════════════════════════════════════╗
  ║        osu! DA+RX Score Backfiller         ║
  ╚════════════════════════════════════════════╝{RESET}
""")

    if not BRIDGE_AVAILABLE:
        print(f"{RED}  osu_pp_bridge not built.{RESET}")
        print(f"  Run:  {CYAN}cd osu_pp_bridge && dotnet build{RESET}\n")
        sys.exit(1)
    if not BRIDGE_REWORK_AVAILABLE:
        print(f"  {YELLOW}osu_pp_bridge_rework not built — rework/no-RX columns will be empty.{RESET}")
        print(f"  Run:  {CYAN}cd osu_pp_bridge_rework && dotnet build{RESET} to enable it\n")

    config_path = os.path.join(_SCRIPT_DIR, "config.ini")
    cfg = configparser.ConfigParser()

    if os.path.exists(config_path) and cfg.read(config_path) and cfg.has_section("osu"):
        client_id     = cfg.get("osu", "client_id",     fallback="").strip()
        client_secret = cfg.get("osu", "client_secret", fallback="").strip()
        user_id       = cfg.get("osu", "user_id",       fallback="").strip()
        if client_id and client_secret and user_id:
            print(f"  {GREY}Loaded credentials from {WHITE}config.ini{RESET}")
        else:
            print(f"  {YELLOW}config.ini found but incomplete — falling back to manual input.{RESET}")
            client_id = client_secret = user_id = ""
    else:
        client_id = client_secret = user_id = ""

    if not client_id or not client_secret or not user_id:
        print(f"  {GREY}Enter your osu! credentials.")
        print(f"  OAuth app: {CYAN}https://osu.ppy.sh/home/account/edit{RESET} → OAuth\n")
        client_id     = input(f"  {GREY}Client ID:     {RESET}").strip()
        client_secret = input(f"  {GREY}Client Secret: {RESET}").strip()
        user_id       = input(f"  {GREY}Your user ID:  {RESET}").strip()

    if not client_id or not client_secret or not user_id:
        print(f"\n{RED}  Missing input. Exiting.{RESET}")
        sys.exit(1)

    print(f"\n  {GREY}Authenticating...{RESET}", end=" ", flush=True)
    try:
        token = get_token(client_id, client_secret)
        print(f"{GREEN}✓{RESET}\n")
    except Exception as e:
        print(f"{RED}✗ {e}{RESET}")
        sys.exit(1)

    db_init()

    # ── Mode selection ────────────────────────────────────────────────────────
    print(f"  {GREY}What would you like to backfill?{RESET}")
    print(f"  {WHITE}1{RESET}  {GREY}Recent scores (your last 100 best + 100 recent){RESET}")
    print(f"  {WHITE}2{RESET}  {GREY}Specific score IDs{RESET}")
    print(f"  {WHITE}3{RESET}  {GREY}Both{RESET}")
    mode = input(f"\n  {GREY}Choice [1/2/3]: {RESET}").strip()
    print()

    total_saved   = 0
    total_skipped = 0
    total_failed  = 0

    # ── Mode 1 / 3: fetch recent + best ──────────────────────────────────────
    if mode in ("1", "3"):
        for score_type in ("best", "recent"):
            print(f"  {CYAN}Fetching {score_type} scores...{RESET}")
            try:
                scores = fetch_user_scores(token, user_id, score_type, limit=100)
            except Exception as e:
                print(f"  {RED}Failed to fetch {score_type} scores: {e}{RESET}\n")
                continue

            da_rx = [s for s in scores if is_da_rx(s.get("mods", []))]
            print(f"  Found {len(da_rx)} DA+RX score(s) in {score_type} (of {len(scores)} total)\n")

            for s in da_rx:
                title     = (s.get("beatmapset") or {}).get("title", "?")
                diff      = (s.get("beatmap")    or {}).get("version", "?")
                mods_disp = format_mods(s.get("mods", []))
                print(f"  {GREY}Calc  {WHITE}{title} [{diff}]{GREY}  {mods_disp}...{RESET}", end=" ", flush=True)
                saved, msg = process_score(s)
                if saved:
                    print(f"{GREEN}{msg}{RESET}")
                    total_saved += 1
                elif "already in database" in msg:
                    print(f"{GREY}SKIP — {msg}{RESET}")
                    total_skipped += 1
                else:
                    print(f"{RED}✗ {msg}{RESET}")
                    total_failed += 1
            print()

    # ── Mode 2 / 3: specific score IDs ───────────────────────────────────────
    if mode in ("2", "3"):
        print(f"  {GREY}Enter score IDs one per line.")
        print(f"  {GREY}You can find them in the URL: osu.ppy.sh/scores/{WHITE}XXXXXXXXX")
        print(f"  {GREY}Leave blank and press Enter when done.{RESET}\n")

        ids = []
        print(f"  {GREY}Adjustments (optional): add after ID, e.g.  {WHITE}5235442671 100=2 miss=1{RESET}")
        print(f"  {GREY}Keys: {WHITE}100{GREY}, {WHITE}50{GREY}, {WHITE}miss{GREY}, {WHITE}sliderend{GREY} (or {WHITE}se{GREY}), {WHITE}largetick{GREY} (or {WHITE}lt{GREY}){RESET}\n")

        while True:
            raw = input(f"  {GREY}Score ID [+ adjustments]: {RESET}").strip()
            if not raw:
                break
            parts = raw.replace(",", " ").split()
            score_part = parts[0]
            adj_parts  = parts[1:]
            sid = None
            if score_part.isdigit():
                sid = int(score_part)
            elif "osu.ppy.sh/scores/" in score_part:
                # e.g. https://osu.ppy.sh/scores/5235442671
                fragment = score_part.rstrip("/").split("/scores/")[-1].split("/")[0]
                if fragment.isdigit():
                    sid = int(fragment)
            if sid is not None:
                adj = parse_adjustments(adj_parts) if adj_parts else {}
                ids.append((sid, adj))
            else:
                print(f"  {YELLOW}  '{score_part}' doesn't look like a score ID or URL, skipping{RESET}")

        if not ids:
            print(f"\n  {GREY}No IDs entered.{RESET}\n")
        else:
            print(f"\n  Processing {len(ids)} score ID(s)...\n")
            for sid, adj in ids:
                adj_desc = ("  " + "  ".join(f"{k}={v}" for k,v in adj.items())) if adj else ""
                print(f"  {GREY}Fetching score {WHITE}{sid}{adj_desc}{GREY}...{RESET}", end=" ", flush=True)
                try:
                    s = fetch_score_by_id(token, sid)
                except Exception as e:
                    print(f"{RED}✗ {e}{RESET}")
                    total_failed += 1
                    continue

                saved, msg = process_score(s, adj=adj if adj else None)
                if saved:
                    label = f"{YELLOW}OVERWRITE{RESET} — " if adj else ""
                    print(f"{GREEN}{label}{msg}{RESET}")
                    total_saved += 1
                elif "already in database" in msg:
                    print(f"{GREY}SKIP — {msg}{RESET}")
                    total_skipped += 1
                else:
                    print(f"{RED}✗ {msg}{RESET}")
                    total_failed += 1
            print()

    if mode not in ("1", "2", "3"):
        print(f"  {YELLOW}Invalid choice. Exiting.{RESET}\n")
        return

    print(f"  {BOLD}Done.{RESET}  "
          f"{GREEN}{total_saved} saved{RESET}, "
          f"{GREY}{total_skipped} already existed{RESET}, "
          f"{RED}{total_failed} failed{RESET}\n")


if __name__ == "__main__":
    main()