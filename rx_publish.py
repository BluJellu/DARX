"""
rx_publish.py — Publish your DA+RX showcase to GitHub Pages
------------------------------------------------------------
Generates rx_showcase.html into docs/index.html, then commits
and pushes so GitHub Pages serves the updated page.

Usage:
  python rx_publish.py                  # publish all scores
  python rx_publish.py --filter ranked
  python rx_publish.py --filter ranked+loved
  python rx_publish.py --limit 100

First-time setup:
  See README.txt — "Hosting on GitHub Pages" section.
"""

import os
import sys
import subprocess
import argparse

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR    = os.path.join(_SCRIPT_DIR, "docs")
OUT_PATH    = os.path.join(DOCS_DIR, "index.html")

RESET  = "\033[0m";  BOLD  = "\033[1m"
GREEN  = "\033[38;5;120m"; CYAN = "\033[38;5;117m"
YELLOW = "\033[38;5;220m"; RED  = "\033[38;5;203m"
GREY   = "\033[38;5;244m"

def run(cmd, cwd=None, capture=False):
    result = subprocess.run(
        cmd, cwd=cwd or _SCRIPT_DIR,
        capture_output=capture, text=True
    )
    if result.returncode != 0:
        if capture:
            print(result.stderr.strip())
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result

def check_git():
    try:
        run(["git", "rev-parse", "--git-dir"], capture=True)
    except Exception:
        print(f"{RED}Not a git repository.{RESET}")
        print(f"Run:  git init  then add your remote and push first.")
        sys.exit(1)

    result = run(["git", "remote", "get-url", "origin"], capture=True)
    remote = result.stdout.strip()
    if not remote:
        print(f"{RED}No git remote 'origin' found.{RESET}")
        print("Add one with:  git remote add origin https://github.com/you/yourrepo.git")
        sys.exit(1)
    return remote

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--filter", default=None,  help="e.g. ranked  or  ranked+loved")
    parser.add_argument("--limit",  default=50,    type=int)
    args = parser.parse_args()

    print(f"\n{BOLD}  DA+RX Showcase Publisher{RESET}\n")

    # ── Check git ──────────────────────────────────────────────────────────────
    remote = check_git()
    print(f"  {GREY}Remote: {CYAN}{remote}{RESET}")

    # ── Generate HTML ─────────────────────────────────────────────────────────
    os.makedirs(DOCS_DIR, exist_ok=True)

    showcase_script = os.path.join(_SCRIPT_DIR, "rx_showcase.py")
    if not os.path.exists(showcase_script):
        print(f"{RED}rx_showcase.py not found.{RESET}")
        sys.exit(1)

    print(f"\n  Generating showcase...")
    cmd = [sys.executable, showcase_script, "--output", OUT_PATH]
    if args.filter:
        cmd += ["--filter", args.filter]
    if args.limit:
        cmd += ["--limit", str(args.limit)]

    result = subprocess.run(cmd, cwd=_SCRIPT_DIR)
    if result.returncode != 0:
        print(f"\n{RED}Showcase generation failed.{RESET}")
        sys.exit(1)

    # ── Git commit & push ─────────────────────────────────────────────────────
    print(f"\n  Committing to git...")

    run(["git", "add", "docs/index.html"])

    # Check if there's actually anything new to commit
    status = run(["git", "status", "--porcelain", "docs/index.html"], capture=True)
    if not status.stdout.strip():
        print(f"  {GREY}No changes — showcase is already up to date.{RESET}")
        _print_url(remote)
        return

    run(["git", "commit", "-m", "Update DA+RX showcase"])
    print(f"  {GREEN}Committed.{RESET}")

    print(f"  Pushing to GitHub...")
    run(["git", "push", "--set-upstream", "origin", "main"])
    print(f"  {GREEN}Pushed!{RESET}")

    _print_url(remote)

def _print_url(remote):
    # Turn  https://github.com/user/repo.git  →  https://user.github.io/repo
    url = ""
    if "github.com" in remote:
        parts = remote.rstrip("/").rstrip(".git").split("/")
        if len(parts) >= 2:
            user, repo = parts[-2], parts[-1]
            # Handle git@ style
            user = user.split(":")[-1]
            url = f"https://{user}.github.io/{repo}"

    print(f"\n  {BOLD}{GREEN}Done!{RESET}")
    if url:
        print(f"  {GREY}Your showcase:{RESET} {CYAN}{url}{RESET}")
    print(f"  {GREY}(GitHub Pages may take ~30s to update){RESET}\n")

if __name__ == "__main__":
    main()