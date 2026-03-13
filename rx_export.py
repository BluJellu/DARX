"""
osu! DA+RX Score Exporter
-------------------------
Dumps every score in rx_scores.db to a plain text file (rx_scores.txt),
sorted by PP descending, one score per line.

Usage:
  python rx_export.py                  # export all scores
  python rx_export.py --filter ranked  # export ranked only
  python rx_export.py --filter ranked+loved

Output file: rx_scores.txt  (next to this script)
"""

import os
import sys
import argparse
import sqlite3
from datetime import datetime

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(_SCRIPT_DIR, "rx_scores.db")
OUT_PATH    = os.path.join(_SCRIPT_DIR, "rx_scores.txt")
BONUS_PP    = 413.894

def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def fetch_all(status_filter=None):
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
            """, statuses).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM scores
                WHERE score_id IN (
                    SELECT score_id FROM scores s
                    WHERE pp = (SELECT MAX(pp) FROM scores WHERE beatmap_id = s.beatmap_id)
                )
                ORDER BY COALESCE(pp_rework, pp_current, pp) DESC
            """).fetchall()
    return rows

def weighted_total(rows, col):
    vals = [r[col] for r in rows if r[col] is not None]
    return sum(v * (0.95 ** i) for i, v in enumerate(vals))

def truncate(s, n):
    s = s or ""
    return s if len(s) <= n else s[:n-1] + "…"

def main():
    parser = argparse.ArgumentParser(description="Export DA+RX scores to text file")
    parser.add_argument("--filter", default=None,
                        help="Status filter, e.g. ranked  or  ranked+loved")
    parser.add_argument("--output", default=OUT_PATH,
                        help="Output file path (default: rx_scores.txt)")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        sys.exit(1)

    rows = fetch_all(args.filter)

    if not rows:
        print("No scores found" + (f" for filter '{args.filter}'" if args.filter else "") + ".")
        sys.exit(0)

    has_rework = any(r["pp_rework"] is not None for r in rows)

    pp_current = weighted_total(rows, "pp_current") + BONUS_PP
    pp_rework  = weighted_total(rows, "pp_rework")  + BONUS_PP if has_rework else None
    pp_norx    = weighted_total(rows, "pp_rework_norx") + BONUS_PP if has_rework else None

    W_RANK  = 4
    W_CUR   = 7
    W_RWK   = 7
    W_NORX  = 7
    W_STARS = 6
    W_ACC   = 7
    W_COMBO = 7
    W_ST    = 5
    W_MODS  = 28
    W_MAP   = 40

    filt_label = f"  Filter: {args.filter.upper()}" if args.filter else "  Filter: ALL"

    lines = []
    lines.append("=" * 110)
    lines.append("  osu! DA+RX Personal Leaderboard — Full Export")
    lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(filt_label)
    lines.append("")

    pp_line = f"  Current PP : {round(pp_current):,}pp"
    if pp_rework is not None:
        pp_line += f"    Rework PP : {round(pp_rework):,}pp"
    if pp_norx is not None:
        pp_line += f"    No-RX PP : {round(pp_norx):,}pp"
    pp_line += f"    ({len(rows)} maps)"
    lines.append(pp_line)
    lines.append("=" * 110)
    lines.append("")

    header = (
        f"  {'#':<{W_RANK}} "
        f"{'CUR':>{W_CUR}} "
        f"{'REWORK':>{W_RWK}} "
        f"{'NO-RX':>{W_NORX}} "
        f"{'STARS':<{W_STARS}} "
        f"{'ACC':>{W_ACC}} "
        f"{'COMBO':>{W_COMBO}} "
        f"{'ST':<{W_ST}} "
        f"{'MODS':<{W_MODS}} "
        f"{'MAP':<{W_MAP}}"
    )
    lines.append(header)
    lines.append("  " + "─" * 108)

    for i, row in enumerate(rows, 1):
        acc       = (row["accuracy"] or 0) * 100
        stars_str = f"{row['stars']:.2f}" if row["stars"] else "?"
        pp_cur    = round(row["pp_current"] or row["pp"] or 0)
        pp_rwk    = f"{round(row['pp_rework']):>{W_RWK}}" if row["pp_rework"] else f"{'—':>{W_RWK}}"
        pp_norx_s = f"{round(row['pp_rework_norx']):>{W_NORX}}" if row["pp_rework_norx"] else f"{'—':>{W_NORX}}"
        bstatus   = (row["beatmap_status"] or "?")[:4].upper()
        mods_str  = truncate(row["mods_display"] or "", W_MODS)
        title     = truncate(f"{row['title']} [{row['diff_name']}]", W_MAP)

        lines.append(
            f"  {i:<{W_RANK}} "
            f"{pp_cur:>{W_CUR}} "
            f"{pp_rwk} "
            f"{pp_norx_s} "
            f"{stars_str:<{W_STARS}} "
            f"{acc:>{W_ACC}.2f}% "
            f"{str(row['combo'])+'x':>{W_COMBO}} "
            f"{bstatus:<{W_ST}} "
            f"{mods_str:<{W_MODS}} "
            f"{title}"
        )

    lines.append("")
    lines.append("=" * 110)

    out = args.output
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Exported {len(rows)} scores to: {out}")

if __name__ == "__main__":
    main()