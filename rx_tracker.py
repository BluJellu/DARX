"""
osu! DA+RX Personal Score Tracker
----------------------------------
Polls the global score feed, filters for your DA+RX scores,
calculates PP via osu_pp_bridge, stores in SQLite, and displays
a live leaderboard sorted by PP that redraws in place.

Setup:
  1. pip install requests
  2. cd osu_pp_bridge && dotnet build   (first run only)
  3. python rx_tracker.py

The osu_pp_bridge/ folder must be next to this script.
"""

import os
import sys
import json
import math
import shutil
import sqlite3
import subprocess
import threading
import time
import traceback
import configparser
import requests

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL  = 60        # seconds between score feed polls
DISPLAY_ROWS   = 30        # max leaderboard rows shown at once
BONUS_PP       = 413.894   # osu! bonus PP from total score count
DB_PATH        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rx_scores.db")
OSU_CACHE_DIR  = os.path.expanduser("~/.cache/osu_lookup")
_SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
_BRIDGE_DIR         = os.path.join(_SCRIPT_DIR, "osu_pp_bridge")
_BRIDGE_BUILT       = (
    os.path.join(_BRIDGE_DIR, "bin", "Debug", "net8.0", "osu_pp_bridge.exe")
    if os.name == "nt" else
    os.path.join(_BRIDGE_DIR, "bin", "Debug", "net8.0", "osu_pp_bridge")
)
_BRIDGE_REWORK_DIR  = os.path.join(_SCRIPT_DIR, "osu_pp_bridge_rework")
_BRIDGE_REWORK_BUILT = (
    os.path.join(_BRIDGE_REWORK_DIR, "bin", "Debug", "net8.0", "osu_pp_bridge_rework.exe")
    if os.name == "nt" else
    os.path.join(_BRIDGE_REWORK_DIR, "bin", "Debug", "net8.0", "osu_pp_bridge_rework")
)

# ── ANSI ──────────────────────────────────────────────────────────────────────
RESET  = "\033[0m";  BOLD   = "\033[1m"
PINK   = "\033[38;5;212m"; CYAN   = "\033[38;5;117m"; GREEN  = "\033[38;5;120m"
YELLOW = "\033[38;5;220m"; RED    = "\033[38;5;203m";  GREY   = "\033[38;5;244m"
WHITE  = "\033[97m"

def clr(text, c): return f"{c}{text}{RESET}"

# osu! beatmap ranked status codes
STATUS_NAMES = {
    -2: "graveyard", -1: "wip", 0: "pending",
     1: "ranked",     2: "approved", 3: "qualified", 4: "loved",
}
STATUS_COLOURS = {
    "ranked":    GREEN,
    "approved":  GREEN,
    "loved":     PINK,
    "qualified": YELLOW,
    "graveyard": GREY,
    "wip":       GREY,
    "pending":   GREY,
    None:        WHITE,
}
# All known statuses for cycling filter
ALL_STATUSES = ["all", "ranked", "approved", "loved", "qualified", "graveyard", "pending", "wip"]

# ── Bridge availability ───────────────────────────────────────────────────────
BRIDGE_AVAILABLE = (
    shutil.which("dotnet") is not None
    and os.path.exists(os.path.join(_BRIDGE_DIR, "osu_pp_bridge.csproj"))
    and os.path.exists(_BRIDGE_BUILT)
)
BRIDGE_REWORK_AVAILABLE = (
    shutil.which("dotnet") is not None
    and os.path.exists(os.path.join(_BRIDGE_REWORK_DIR, "osu_pp_bridge_rework.csproj"))
    and os.path.exists(_BRIDGE_REWORK_BUILT)
)

# ── SQLite ────────────────────────────────────────────────────────────────────
def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_init():
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                score_id        INTEGER PRIMARY KEY,
                beatmap_id      INTEGER,
                title           TEXT,
                diff_name       TEXT,
                stars           REAL,
                pp              REAL,
                pp_current      REAL,
                pp_rework       REAL,
                pp_rework_norx  REAL,
                accuracy        REAL,
                combo           INTEGER,
                n300            INTEGER,
                n100            INTEGER,
                n50             INTEGER,
                misses          INTEGER,
                mods_display    TEXT,
                score_date      TEXT,
                beatmap_status  TEXT,
                added_at        TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Migrate: add new pp columns if upgrading from older schema
        for col, typedef in [
            ("pp_current",     "REAL"),
            ("pp_rework",      "REAL"),
            ("pp_rework_norx", "REAL"),
            ("beatmap_status", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE scores ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # column already exists

def db_get_meta(key, default=None):
    with db_connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

def db_set_meta(key, value):
    with db_connect() as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, str(value)))

def db_insert_score(score_id, beatmap_id, title, diff_name, stars, pp,
                    accuracy, combo, n300, n100, n50, misses, mods_display, score_date,
                    pp_current=None, pp_rework=None, pp_rework_norx=None,
                    beatmap_status=None):
    with db_connect() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO scores
            (score_id, beatmap_id, title, diff_name, stars, pp,
             pp_current, pp_rework, pp_rework_norx,
             accuracy, combo, n300, n100, n50, misses, mods_display, score_date,
             beatmap_status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (score_id, beatmap_id, title, diff_name, stars, pp,
              pp_current if pp_current is not None else pp,
              pp_rework,
              pp_rework_norx,
              accuracy, combo, n300, n100, n50, misses, mods_display, score_date,
              beatmap_status))

def db_load_leaderboard(status_filter=None):
    """Load best-per-map scores, optionally filtered by beatmap_status."""
    if status_filter and status_filter != "all":
        statuses = [s.strip() for s in status_filter.split("+")]
        placeholders = ",".join("?" * len(statuses))
        sql = f"""
            SELECT * FROM scores
            WHERE score_id IN (
                SELECT score_id FROM scores s
                WHERE pp = (SELECT MAX(pp) FROM scores WHERE beatmap_id = s.beatmap_id)
            )
            AND COALESCE(beatmap_status, 'unknown') IN ({placeholders})
            ORDER BY COALESCE(pp_rework, pp_current, pp) DESC LIMIT ?
        """
        with db_connect() as conn:
            return conn.execute(sql, (*statuses, DISPLAY_ROWS)).fetchall()
    else:
        with db_connect() as conn:
            return conn.execute("""
                SELECT * FROM scores
                WHERE score_id IN (
                    SELECT score_id FROM scores s
                    WHERE pp = (SELECT MAX(pp) FROM scores WHERE beatmap_id = s.beatmap_id)
                )
                ORDER BY COALESCE(pp_rework, pp_current, pp) DESC LIMIT ?
            """, (DISPLAY_ROWS,)).fetchall()

def db_count(status_filter=None):
    """Count of unique beatmaps with scores, optionally filtered by status."""
    with db_connect() as conn:
        if status_filter and status_filter != "all":
            statuses = [s.strip() for s in status_filter.split("+")]
            ph = ",".join("?" * len(statuses))
            return conn.execute(
                f"SELECT COUNT(DISTINCT beatmap_id) FROM scores WHERE COALESCE(beatmap_status,'unknown') IN ({ph})",
                statuses
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(DISTINCT beatmap_id) FROM scores").fetchone()[0]

def db_total_pp(status_filter=None):
    """Weighted total PP for all 3 variants. Returns dict with keys current/rework/norx."""
    with db_connect() as conn:
        if status_filter and status_filter != "all":
            statuses = [s.strip() for s in status_filter.split("+")]
            ph = ",".join("?" * len(statuses))
            rows = conn.execute(f"""
                SELECT
                    MAX(COALESCE(pp_current, pp)) as pp_current,
                    MAX(pp_rework)                as pp_rework,
                    MAX(pp_rework_norx)           as pp_rework_norx
                FROM scores
                WHERE COALESCE(beatmap_status, 'unknown') IN ({ph})
                GROUP BY beatmap_id
                ORDER BY COALESCE(MAX(pp_rework), MAX(pp_current), MAX(pp)) DESC
            """, statuses).fetchall()
        else:
            rows = conn.execute("""
                SELECT
                    MAX(COALESCE(pp_current, pp)) as pp_current,
                    MAX(pp_rework)                as pp_rework,
                    MAX(pp_rework_norx)           as pp_rework_norx
                FROM scores GROUP BY beatmap_id
                ORDER BY COALESCE(MAX(pp_rework), MAX(pp_current), MAX(pp)) DESC
            """).fetchall()
    def weighted(vals):
        return sum(v * (0.95 ** i) for i, v in enumerate(vals) if v is not None)
    return {
        "current": weighted([r["pp_current"]    for r in rows]),
        "rework":  weighted([r["pp_rework"]      for r in rows]),
        "norx":    weighted([r["pp_rework_norx"] for r in rows]),
    }

# ── Mod helpers ───────────────────────────────────────────────────────────────
def has_mod(mods_list, acronym):
    return any(
        (m if isinstance(m, str) else m.get("acronym", "")).upper() == acronym
        for m in (mods_list or [])
    )

def is_da_rx(mods_list):
    return has_mod(mods_list, "DA") and has_mod(mods_list, "RX")

def format_mods(mods_list):
    parts = []
    for m in (mods_list or []):
        acronym  = (m if isinstance(m, str) else m.get("acronym", "")).upper()
        settings = {} if isinstance(m, str) else (m.get("settings") or {})
        if not acronym or acronym == "NM":
            continue
        extras = []
        if acronym in ("DT", "NC"):
            r = settings.get("speed_change")
            if r is not None and abs(r - 1.5) > 0.001:
                extras.append(f"{r:g}x")
        elif acronym in ("HT", "DC"):
            r = settings.get("speed_change")
            if r is not None and abs(r - 0.75) > 0.001:
                extras.append(f"{r:g}x")
        elif acronym == "DA":
            for key, label in [("approach_rate","AR"),("overall_difficulty","OD"),
                                ("circle_size","CS"),("drain_rate","HP")]:
                v = settings.get(key)
                if v is not None:
                    extras.append(f"{label}{v:g}")
        parts.append(f"{acronym}({'  '.join(extras)})" if extras else acronym)
    return " ".join(parts) if parts else "NM"

def get_mods_for_bridge(mods_list):
    result = []
    SKIP_SETTINGS = {"adjust_pitch"}
    for m in (mods_list or []):
        if isinstance(m, str):
            if m.upper() != "NM":
                result.append({"acronym": m.upper()})
        else:
            acronym = m.get("acronym", "").upper()
            if acronym and acronym != "NM":
                obj = {"acronym": acronym}
                raw = m.get("settings") or {}
                filtered = {k: v for k, v in raw.items() if k not in SKIP_SETTINGS}
                if filtered:
                    obj["settings"] = filtered
                result.append(obj)
    return result

# ── API ───────────────────────────────────────────────────────────────────────
def get_token(client_id, client_secret):
    resp = requests.post(
        "https://osu.ppy.sh/oauth/token",
        json={"client_id": client_id, "client_secret": client_secret,
              "grant_type": "client_credentials", "scope": "public"},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise ValueError("No access_token in response")
    return token

def fetch_scores_page(token, cursor_string=None):
    """Fetch one page from the global score feed."""
    headers = {"Authorization": f"Bearer {token}",
               "x-api-version": "20220705"}
    params  = {"ruleset": "osu"}
    if cursor_string:
        params["cursor_string"] = cursor_string
    resp = requests.get("https://osu.ppy.sh/api/v2/scores",
                        headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    return data.get("scores", []), data.get("cursor_string")

def fetch_osu_file(beatmap_id):
    os.makedirs(OSU_CACHE_DIR, exist_ok=True)
    path = os.path.join(OSU_CACHE_DIR, f"{beatmap_id}.osu")
    if not os.path.exists(path):
        resp = requests.get(f"https://osu.ppy.sh/osu/{beatmap_id}", timeout=15)
        resp.raise_for_status()
        with open(path, "wb") as f:
            f.write(resp.content)
    return path

# ── PP bridge ─────────────────────────────────────────────────────────────────
def calculate_pp(score, osu_path, bridge_dir=None, override_mods=None):
    """Calculate PP via the specified bridge. override_mods replaces the score's mod list."""
    if bridge_dir is None:
        bridge_dir = _BRIDGE_DIR
    stats   = score.get("statistics", {})
    n300    = stats.get("great")  or stats.get("count_300")  or 0
    n100    = stats.get("ok")     or stats.get("count_100")  or 0
    n50     = stats.get("meh")    or stats.get("count_50")   or 0
    misses  = stats.get("miss")   or stats.get("count_miss") or 0
    combo   = score.get("max_combo") or 0
    sth     = stats.get("slider_tail_hit")
    lth     = stats.get("large_tick_hit") or 0
    smth    = stats.get("ignore_hit") or stats.get("small_tick_hit") or 0
    is_lazer = sth is not None
    mods    = override_mods if override_mods is not None else score.get("mods", [])

    payload = {
        "beatmap_path":    os.path.abspath(osu_path),
        "lazer":           is_lazer,
        "combo":           combo,
        "n300": n300, "n100": n100, "n50": n50, "misses": misses,
        "slider_end_hits": sth if sth is not None else 0,
        "large_tick_hits": lth,
        "small_tick_hits": smth,
        "mods":            get_mods_for_bridge(mods),
        "accuracy":        score.get("accuracy", 1.0),
    }

    result = subprocess.run(
        ["dotnet", "run", "--project", bridge_dir, "--no-build"],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"bridge exit {result.returncode}")

    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError("bridge produced no output")
    data = json.loads(lines[-1])
    return data.get("pp", 0.0), data.get("stars", 0.0)

def mods_without_rx(mods_list):
    """Return mods list with RX removed."""
    return [m for m in (mods_list or [])
            if (m if isinstance(m, str) else m.get("acronym", "")).upper() != "RX"]

# ── Display ───────────────────────────────────────────────────────────────────
_status_msg   = "Starting up..."
_status_lock  = threading.Lock()
_filter_index = 0          # index into ALL_STATUSES
_filter_lock  = threading.Lock()

def get_filter():
    with _filter_lock:
        return ALL_STATUSES[_filter_index]

def cycle_filter(direction=1):
    with _filter_lock:
        global _filter_index
        _filter_index = (_filter_index + direction) % len(ALL_STATUSES)
        return ALL_STATUSES[_filter_index]

def keyboard_worker():
    """Background thread: reads single keypresses to cycle the status filter."""
    import tty, termios
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\x03", "q"):   # Ctrl-C or q → quit
                os.kill(os.getpid(), 2)
            elif ch in ("f", "F", "\t"):  # f / Tab → cycle forward
                cycle_filter(1)
            elif ch in ("b", "B"):         # b → cycle backward
                cycle_filter(-1)
    except Exception:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def set_status(msg):
    with _status_lock:
        global _status_msg
        _status_msg = msg

def get_status():
    with _status_lock:
        return _status_msg

def truncate(s, n):
    s = str(s)
    return s if len(s) <= n else s[:n-1] + "…"

def draw_leaderboard():
    filt      = get_filter()
    rows      = db_load_leaderboard(filt)
    total     = db_count(filt)
    total_pp  = db_total_pp(filt)
    status    = get_status()

    lines = []
    # Header
    lines.append(f"{PINK}{BOLD}  ╔════════════════════════════════════════════╗")
    lines.append(f"  ║     osu! DA+RX Personal Leaderboard        ║")
    lines.append(f"  ╚════════════════════════════════════════════╝{RESET}")
    pp_cur  = round(total_pp["current"] + BONUS_PP)
    pp_rwk  = round(total_pp["rework"])  if total_pp["rework"]  else None
    pp_norx = round(total_pp["norx"])    if total_pp["norx"]    else None

    rework_str = f"  {GREY}│ Rework: {PINK}{BOLD}{pp_rwk:,}pp{RESET}" if pp_rwk else f"  {GREY}│ Rework: {GREY}(bridge not built){RESET}"
    norx_str   = f"  {GREY}│ No-RX:  {CYAN}{BOLD}{pp_norx:,}pp{RESET}" if pp_norx else f"  {GREY}│ No-RX:  {GREY}(bridge not built){RESET}"

    filt_colour = STATUS_COLOURS.get(filt, WHITE)
    filt_label  = filt.upper() if filt != "all" else "ALL"
    lines.append(f"  {GREY}Current PP: {YELLOW}{BOLD}{pp_cur:,}pp{RESET}{rework_str}{norx_str}   {GREY}({total} maps){RESET}")
    lines.append(f"  {GREY}Filter: {filt_colour}{BOLD}{filt_label}{RESET}   {GREY}[F] next  [B] prev  [Q] quit   Poll: {CYAN}{status}{RESET}")
    lines.append("")

    if not rows:
        lines.append(f"  {GREY}No DA+RX scores recorded yet. Waiting for new scores...{RESET}")
    else:
        # Column widths
        W_RANK  = 4
        W_CUR   = 7
        W_RWK   = 7
        W_NORX  = 7
        W_STARS = 6
        W_ACC   = 7
        W_COMBO = 7
        W_MODS  = 20
        W_MAP   = 28

        header = (
            f"  {GREY}"
            f"{'#':<{W_RANK}} "
            f"{'CUR':>{W_CUR}} "
            f"{'REWORK':>{W_RWK}} "
            f"{'NO-RX':>{W_NORX}} "
            f"{'★':<{W_STARS}} "
            f"{'ACC':>{W_ACC}} "
            f"{'COMBO':>{W_COMBO}} "
            f"{'ST':>3} "
            f"{'MODS':<{W_MODS}} "
            f"{'MAP':<{W_MAP}}"
            f"{RESET}"
        )
        lines.append(header)
        lines.append(f"  {GREY}{'─'*103}{RESET}")

        for i, row in enumerate(rows, 1):
            rank_col  = YELLOW if i == 1 else (GREY if i > 10 else WHITE)
            pp_col    = GREEN if i <= 3 else (CYAN if i <= 10 else WHITE)
            acc       = row["accuracy"] * 100
            acc_col   = GREEN if acc >= 99 else (CYAN if acc >= 97 else WHITE)
            mods_str  = truncate(row["mods_display"], W_MODS)
            title     = truncate(f"{row['title']} [{row['diff_name']}]", W_MAP)
            stars_str = f"{row['stars']:.2f}" if row['stars'] else "?"
            pp_cur    = round(row["pp_current"] or row["pp"] or 0)
            pp_rwk    = f"{round(row['pp_rework']):>{W_RWK}}" if row["pp_rework"] else f"{'—':>{W_RWK}}"
            pp_norx   = f"{round(row['pp_rework_norx']):>{W_NORX}}" if row["pp_rework_norx"] else f"{'—':>{W_NORX}}"

            bstatus    = row["beatmap_status"] or "?"
            bstatus_c  = STATUS_COLOURS.get(bstatus, WHITE)
            bstatus_s  = f"{bstatus_c}{bstatus[:3].upper():>3}{RESET}"

            lines.append(
                f"  {rank_col}{i:<{W_RANK}}{RESET} "
                f"{pp_col}{pp_cur:>{W_CUR}}{RESET} "
                f"{PINK}{pp_rwk}{RESET} "
                f"{CYAN}{pp_norx}{RESET} "
                f"{YELLOW}{stars_str:<{W_STARS}}{RESET} "
                f"{acc_col}{acc:>{W_ACC}.2f}%{RESET} "
                f"{WHITE}{str(row['combo'])+'x':>{W_COMBO}}{RESET} "
                f"{bstatus_s} "
                f"{YELLOW}{mods_str:<{W_MODS}}{RESET} "
                f"{GREY}{title}{RESET}"
            )

        if total > DISPLAY_ROWS:
            lines.append(f"\n  {GREY}… and {total - DISPLAY_ROWS} more in the database{RESET}")

    lines.append(f"\n  {GREY}Press Ctrl+C to quit{RESET}")

    # Redraw: clear entire screen, move cursor to top-left, print
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()

# ── Polling worker ────────────────────────────────────────────────────────────
def poll_worker(token, user_id):
    """Background thread: polls the score feed, filters, calculates PP, saves."""
    cursor_string = db_get_meta("cursor_string")
    first_run     = cursor_string is None

    if first_run:
        # On first run, fast-forward to current position so we don't process
        # the entire history of all osu! scores (that would take forever).
        # We fetch one page just to get the latest cursor, then start from there.
        set_status("Initialising cursor position...")
        try:
            _, cursor_string = fetch_scores_page(token, cursor_string=None)
            if cursor_string:
                db_set_meta("cursor_string", cursor_string)
            set_status(f"Ready — watching for new DA+RX scores from user {user_id}")
        except Exception as e:
            set_status(f"Init error: {e}")
        return   # Don't process any scores on first run; wait for next poll

    while True:
        try:
            set_status(f"Polling score feed...")
            scores, new_cursor = fetch_scores_page(token, cursor_string)

            new_count = 0
            for s in scores:
                # Filter: must be this user and have both DA and RX
                uid  = (s.get("user_id")
                        or s.get("user", {}).get("id"))
                mods = s.get("mods", [])
                if str(uid) != str(user_id) or not is_da_rx(mods):
                    continue

                score_id   = s.get("id")
                beatmap    = s.get("beatmap")   or {}
                beatmapset = s.get("beatmapset") or {}
                beatmap_id = beatmap.get("id")

                if not beatmap_id or not score_id:
                    continue

                # Skip if already stored
                with db_connect() as conn:
                    exists = conn.execute(
                        "SELECT 1 FROM scores WHERE score_id=?", (score_id,)
                    ).fetchone()
                if exists:
                    continue

                set_status(f"New score found (id={score_id}) — calculating PP...")
                try:
                    osu_path      = fetch_osu_file(beatmap_id)
                    pp_c, stars   = calculate_pp(s, osu_path, _BRIDGE_DIR)
                    pp_r          = None
                    pp_norx       = None
                    if BRIDGE_REWORK_AVAILABLE:
                        pp_r,  _  = calculate_pp(s, osu_path, _BRIDGE_REWORK_DIR)
                        pp_norx, _ = calculate_pp(s, osu_path, _BRIDGE_REWORK_DIR,
                                                   override_mods=mods_without_rx(s.get("mods", [])))
                except Exception as e:
                    set_status(f"PP calc failed for {score_id}: {e}")
                    time.sleep(2)
                    continue

                stats   = s.get("statistics", {})
                title   = beatmapset.get("title", "?")
                diff    = beatmap.get("version", "?")
                bstatus = STATUS_NAMES.get(beatmap.get("ranked"), None)
                acc     = s.get("accuracy", 0.0)
                combo   = s.get("max_combo", 0)
                n300    = stats.get("great") or stats.get("count_300") or 0
                n100    = stats.get("ok")    or stats.get("count_100") or 0
                n50     = stats.get("meh")   or stats.get("count_50")  or 0
                nm      = stats.get("miss")  or stats.get("count_miss") or 0
                date    = (s.get("ended_at") or s.get("created_at") or "")[:19]
                mdisp   = format_mods(mods)

                db_insert_score(score_id, beatmap_id, title, diff, stars, pp_c,
                                acc, combo, n300, n100, n50, nm, mdisp, date,
                                pp_current=pp_c, pp_rework=pp_r, pp_rework_norx=pp_norx,
                                beatmap_status=bstatus)
                new_count += 1
                set_status(f"Saved: {title} [{diff}]  {round(pp_c)}pp current" +
                           (f" / {round(pp_r)}pp rework" if pp_r else ""))

            if new_cursor:
                cursor_string = new_cursor
                db_set_meta("cursor_string", cursor_string)

            msg = (f"Last poll: {time.strftime('%H:%M:%S')} — "
                   f"{new_count} new score(s). Next in {POLL_INTERVAL}s")
            set_status(msg)

        except requests.HTTPError as e:
            status_code = e.response.status_code if e.response else "?"
            if status_code == 401:
                set_status("Token expired — restart the tracker")
                return
            set_status(f"HTTP {status_code} — retrying next poll")
        except Exception as e:
            set_status(f"Poll error: {e}")

        time.sleep(POLL_INTERVAL)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Check bridge
    if not BRIDGE_AVAILABLE:
        print(f"\n{RED}  osu_pp_bridge not built.{RESET}")
        print(f"  Run:  {CYAN}cd osu_pp_bridge && dotnet build{RESET}\n")
        sys.exit(1)
    if not BRIDGE_REWORK_AVAILABLE:
        print(f"  {YELLOW}osu_pp_bridge_rework not built — rework PP will show as —{RESET}")
        print(f"  Run:  {CYAN}cd osu_pp_bridge_rework && dotnet build{RESET} to enable it\n")

    # Banner
    print(f"""
{PINK}{BOLD}  ╔════════════════════════════════════════════╗
  ║     osu! DA+RX Personal Leaderboard        ║
  ╚════════════════════════════════════════════╝{RESET}
""")

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
        print(f"{GREEN}✓{RESET}")
    except Exception as e:
        print(f"{RED}✗ {e}{RESET}")
        sys.exit(1)

    # Init DB
    db_init()

    # Start polling thread
    t = threading.Thread(target=poll_worker, args=(token, user_id), daemon=True)
    t.start()

    kb = threading.Thread(target=keyboard_worker, daemon=True)
    kb.start()

    # Main loop: redraw leaderboard every 3 seconds
    # First run: poll_worker will fast-forward cursor then return;
    # subsequent polls happen on the timer inside poll_worker.
    # Give it a moment to init before clearing the screen.
    time.sleep(1.5)

    try:
        while True:
            draw_leaderboard()
            time.sleep(3)
    except KeyboardInterrupt:
        print(f"\n\n  {GREY}Bye!{RESET}\n")


if __name__ == "__main__":
    main()