"""
osu! DA+RX PP Recalculator
---------------------------
Updates pp_current, pp_rework, and pp_rework_norx columns for all
scores already in the database.

Run this:
  - After building osu_pp_bridge_rework for the first time
  - After a pp algorithm update (rebuild the relevant bridge first)
  - With --column to target only one column

Usage:
    python3 rx_recalc.py                  # recalc all 3 columns
    python3 rx_recalc.py --column current # recalc pp_current only
    python3 rx_recalc.py --column rework  # recalc pp_rework + pp_rework_norx only
"""

import os
import sys
import json
import sqlite3
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

from rx_tracker import (
    db_connect, fetch_osu_file, calculate_pp, mods_without_rx,
    BRIDGE_AVAILABLE, BRIDGE_REWORK_AVAILABLE,
    _BRIDGE_DIR, _BRIDGE_REWORK_DIR,
    RESET, BOLD, PINK, CYAN, GREEN, YELLOW, RED, GREY, WHITE,
)

def clr(text, c): return f"{c}{text}{RESET}"

def recalc_all(column_filter=None):
    print(f"""
{PINK}{BOLD}  ╔════════════════════════════════════════════╗
  ║        osu! DA+RX PP Recalculator          ║
  ╚════════════════════════════════════════════╝{RESET}
""")

    do_current = column_filter in (None, "current")
    do_rework  = column_filter in (None, "rework")

    if do_current and not BRIDGE_AVAILABLE:
        print(f"{RED}  osu_pp_bridge not built.{RESET}")
        print(f"  Run:  {CYAN}cd osu_pp_bridge && dotnet build{RESET}\n")
        if column_filter == "current":
            sys.exit(1)
        do_current = False

    if do_rework and not BRIDGE_REWORK_AVAILABLE:
        print(f"{RED}  osu_pp_bridge_rework not built.{RESET}")
        print(f"  Run:  {CYAN}cd osu_pp_bridge_rework && dotnet build{RESET}\n")
        if column_filter == "rework":
            sys.exit(1)
        do_rework = False

    if not do_current and not do_rework:
        print(f"{RED}  Nothing to recalculate — no bridges available.{RESET}\n")
        sys.exit(1)

    cols = []
    if do_current: cols.append("pp_current")
    if do_rework:  cols.extend(["pp_rework", "pp_rework_norx"])
    print(f"  Recalculating columns: {CYAN}{', '.join(cols)}{RESET}\n")

    with db_connect() as conn:
        rows = conn.execute("""
            SELECT score_id, beatmap_id, mods_display,
                   title, diff_name, pp, pp_current, pp_rework, pp_rework_norx,
                   accuracy, max_combo,
                   n300, n100, n50, misses,
                   score_date
            FROM scores ORDER BY pp DESC
        """).fetchall()

    total   = len(rows)
    success = 0
    failed  = 0

    for idx, row in enumerate(rows, 1):
        score_id   = row["score_id"]
        beatmap_id = row["beatmap_id"]
        title      = row["title"]
        diff       = row["diff_name"]
        print(f"  [{idx}/{total}] {GREY}{title} [{diff}]{RESET}...", end=" ", flush=True)

        # Reconstruct a minimal score dict for calculate_pp
        # We need to pull the full score's mods from the DB mods_display.
        # But calculate_pp needs the raw mods list — we stored mods_display (formatted string).
        # Instead we'll re-fetch the .osu and pass the stored stats directly.
        # We store enough hit data in the DB to reconstruct the payload.
        fake_score = {
            "accuracy":  row["accuracy"],
            "max_combo": row["max_combo"],
            "statistics": {
                "great": row["n300"],
                "ok":    row["n100"],
                "meh":   row["n50"],
                "miss":  row["misses"],
            },
            "mods": [],  # will be overridden — see note below
        }

        # We can't perfectly reconstruct the original mod objects from mods_display.
        # Instead re-fetch the score from the DB raw_mods column if we have it,
        # otherwise use the stored mods_display as a fallback with acronym-only mods.
        # For now: store raw mod JSON when saving scores (see note at bottom).
        # Fallback: parse acronyms from mods_display string.
        mods_raw = _parse_mods_display(row["mods_display"])
        fake_score["mods"] = mods_raw

        try:
            osu_path = fetch_osu_file(beatmap_id)
        except Exception as e:
            print(f"{RED}✗ fetch .osu: {e}{RESET}")
            failed += 1
            continue

        pp_c = pp_r = pp_norx = None
        errors = []

        if do_current:
            try:
                pp_c, _ = calculate_pp(fake_score, osu_path, _BRIDGE_DIR)
            except Exception as e:
                errors.append(f"current: {e}")

        if do_rework:
            try:
                pp_r,  _ = calculate_pp(fake_score, osu_path, _BRIDGE_REWORK_DIR)
                norx_mods = mods_without_rx(mods_raw)
                pp_norx, _ = calculate_pp(fake_score, osu_path, _BRIDGE_REWORK_DIR,
                                          override_mods=norx_mods)
            except Exception as e:
                errors.append(f"rework: {e}")

        if errors:
            print(f"{RED}✗ {'; '.join(errors)}{RESET}")
            failed += 1
            continue

        # Update DB
        updates = {}
        if pp_c    is not None: updates["pp_current"]     = pp_c
        if pp_r    is not None: updates["pp_rework"]      = pp_r
        if pp_norx is not None: updates["pp_rework_norx"] = pp_norx

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            vals       = list(updates.values()) + [score_id]
            with db_connect() as conn:
                conn.execute(f"UPDATE scores SET {set_clause} WHERE score_id=?", vals)

        parts = []
        if pp_c    is not None: parts.append(f"cur {round(pp_c)}pp")
        if pp_r    is not None: parts.append(f"rework {round(pp_r)}pp")
        if pp_norx is not None: parts.append(f"no-RX {round(pp_norx)}pp")
        print(f"{GREEN}✓  {' / '.join(parts)}{RESET}")
        success += 1

    print(f"\n  {BOLD}Done.{RESET}  {GREEN}{success} updated{RESET}, {RED}{failed} failed{RESET}\n")
    if failed > 0:
        print(f"  {YELLOW}Note: failures are usually due to missing mod settings (e.g. exact AR/speed).")
        print(f"  The recalculator reconstructs mods from the stored display string, which")
        print(f"  loses exact numeric settings. For perfect accuracy, re-run rx_backfill.py")
        print(f"  after clearing the affected scores from the DB.{RESET}\n")


def _parse_mods_display(mods_display):
    """
    Reconstruct a basic mod list from the formatted display string.
    e.g. "DT(1.8x) HD DA(AR9 OD0 CS0 HP0) RX" → list of mod dicts

    This is best-effort: numeric settings inside () are re-parsed where possible.
    """
    import re
    result = []
    # Split on spaces but keep parenthesised groups attached
    tokens = re.findall(r'[A-Z]+(?:\([^)]*\))?', mods_display or "")
    for token in tokens:
        m = re.match(r'([A-Z]+)(?:\(([^)]*)\))?', token)
        if not m:
            continue
        acronym  = m.group(1)
        settings_str = m.group(2) or ""
        settings = {}

        if acronym in ("DT", "NC", "HT", "DC") and settings_str:
            rate = re.search(r'([\d.]+)x', settings_str)
            if rate:
                settings["speed_change"] = float(rate.group(1))

        if acronym == "DA" and settings_str:
            for key, label in [("approach_rate","AR"), ("overall_difficulty","OD"),
                                ("circle_size","CS"), ("drain_rate","HP")]:
                v = re.search(label + r'([\d.]+)', settings_str)
                if v:
                    settings[key] = float(v.group(1))

        obj = {"acronym": acronym}
        if settings:
            obj["settings"] = settings
        result.append(obj)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--column", choices=["current", "rework"],
                        help="Recalculate only one set of columns")
    args = parser.parse_args()
    recalc_all(column_filter=args.column)
