━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  osu! DA+RX Personal Leaderboard & Tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Tracks your DA+RX scores in osu!, calculates PP using the actual
osu! algorithm, and displays a live leaderboard in your terminal.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FILES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  rx_tracker.py
      The main script. Polls the osu! global score feed every 60s,
      picks up your DA+RX scores, calculates PP, and displays a
      live terminal leaderboard sorted by PP. Press F/B to cycle
      through status filters (all, ranked, loved, etc). Press Q to quit.

  rx_backfill.py
      Adds existing scores to the database. Offers three modes:
        1 - scan your last 100 best + 100 recent scores
        2 - enter specific score IDs manually
        3 - both
      When entering score IDs you can append stat corrections for
      buggy scores, e.g.:  5235442671 miss=1 100=2 se=3

  rx_recalc.py
      Recalculates PP columns for all scores already in the database.
      Use this after rebuilding a bridge to update stored values.
        python3 rx_recalc.py                  (all columns)
        python3 rx_recalc.py --column current (pp_current only)
        python3 rx_recalc.py --column rework  (pp_rework + pp_rework_norx only)

  config.ini
      Stores your osu! OAuth credentials and user ID so you don't
      have to type them on every run. Fill this in before running
      anything else (see setup below).

  rx_scores.db
      SQLite database created automatically on first run. Stores all
      your scores. Safe to keep across machines — just copy it over.

  osu_pp_bridge/
      C# project that wraps the actual ppy osu! NuGet packages to
      calculate PP. Pinned to a specific version — this is your
      "current live" PP reference. Must be built before use.

  osu_pp_bridge_rework/
      Identical bridge but with a floating (*) version. When a PP
      rework ships, rebuild this folder and run rx_recalc.py to
      fill in the rework columns. Never rebuild osu_pp_bridge/ unless
      you intentionally want to update the "current" baseline.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  REQUIREMENTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  - Python 3.9+
  - .NET 8 SDK   https://dotnet.microsoft.com/download
  - pip install requests


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SETUP (first time on a new machine)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Install Python dependencies

        pip install requests

  2. Create an osu! OAuth application

        Go to: https://osu.ppy.sh/home/account/edit  (scroll to OAuth)
        Click "New OAuth Application"
        Set the callback URL to:  http://localhost
        Copy the Client ID and Client Secret

  3. Fill in config.ini

        Open config.ini and replace the placeholder values:

          [osu]
          client_id     = 12345
          client_secret = abc123xyz
          user_id       = 67890

        Your user ID is the number in your osu! profile URL:
          https://osu.ppy.sh/users/67890

  4. Build the PP bridges

        cd osu_pp_bridge
        dotnet build
        cd ..

        cd osu_pp_bridge_rework
        dotnet build
        cd ..

        The first build takes a few minutes (downloads ~300MB of ppy
        packages). Subsequent builds are fast.

        If you see restore errors about a missing NuGet source,
        check that NuGet.Config inside the bridge folder contains:

          <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />

  5. Backfill your existing scores

        python3 rx_backfill.py

        Choose option 1 to scan your recent/best scores, or option 2
        to add specific scores by ID.

  6. Start the live tracker

        python3 rx_tracker.py


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHEN A PP REWORK SHIPS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        cd osu_pp_bridge_rework
        dotnet build          ← pulls the new algorithm automatically
        cd ..
        python3 rx_recalc.py --column rework

  The leaderboard will then show three PP columns:
    CUR     — PP in the version you pinned in osu_pp_bridge/
    REWORK  — PP in the latest rework build
    NO-RX   — Rework PP simulated without the RX mod


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MOVING TO A NEW MACHINE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Copy the entire folder including rx_scores.db. Then just follow
  steps 1 and 4 above (install Python deps + dotnet build).
  Your scores, credentials, and history carry over automatically.


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FOLDER STRUCTURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  osu_stuff/
  ├── rx_tracker.py
  ├── rx_backfill.py
  ├── rx_recalc.py
  ├── config.ini                ← fill this in, keep it private
  ├── rx_scores.db              ← created automatically
  ├── README.txt
  ├── osu_pp_bridge/
  │   ├── Program.cs
  │   ├── osu_pp_bridge.csproj  ← pinned version
  │   └── NuGet.Config
  └── osu_pp_bridge_rework/
      ├── Program.cs
      ├── osu_pp_bridge_rework.csproj  ← floating * version
      └── NuGet.Config


━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOSTING ON GITHUB PAGES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  This lets anyone visit a link and see your top plays as a
  styled web page. You only do this setup once.


  STEP 1 — Create a GitHub account (skip if you have one)
  ─────────────────────────────────────────────────────────
  Go to https://github.com and sign up for a free account.


  STEP 2 — Install Git (skip if already installed)
  ─────────────────────────────────────────────────────────
  Git is the tool that uploads your files to GitHub.

  Windows:  Download from https://git-scm.com/download/win
            Run the installer, leave all options as default.
            Open a new Command Prompt after installing.

  Mac:      Open Terminal and run:
              git --version
            If it's not installed, macOS will prompt you to
            install it automatically.

  To check it worked, open a terminal and run:
    git --version
  You should see something like: git version 2.x.x


  STEP 3 — Create a repository on GitHub
  ─────────────────────────────────────────────────────────
  A repository ("repo") is where your files live on GitHub.

  1. Go to https://github.com/new
  2. Fill in:
       Repository name:  osu-darx-scores  (or whatever you like)
       Visibility:       Public
                         (must be public for free GitHub Pages)
  3. Leave everything else unchecked/default.
  4. Click "Create repository".

  You'll land on an empty repo page. Leave this tab open.


  STEP 4 — Tell Git who you are (first time only)
  ─────────────────────────────────────────────────────────
  Open a terminal in your osu_stuff folder and run these two
  commands, replacing the values with your own:

    git config --global user.name "Your Name"
    git config --global user.email "you@example.com"

  This doesn't have to match your GitHub account exactly —
  it's just a label that appears on commits.


  STEP 5 — Connect your folder to GitHub
  ─────────────────────────────────────────────────────────
  In your terminal, make sure you're inside the osu_stuff
  folder (the one containing rx_tracker.py), then run:

    git init
    git branch -M main

  Now copy the URL of your repo from the GitHub tab you left
  open. It looks like:
    https://github.com/yourusername/osu-darx-scores

  Run this (paste your own URL):
    git remote add origin https://github.com/yourusername/osu-darx-scores.git

  (note the .git at the end)


  STEP 6 — Upload your files
  ─────────────────────────────────────────────────────────
  Run these three commands one at a time:

    git add .
    git commit -m "Initial commit"
    git push -u origin main

  The first "git push" will ask you to log in to GitHub.
  The easiest way is a Personal Access Token:

    1. Go to https://github.com/settings/tokens/new
    2. Give it a name (e.g. "osu-tracker")
    3. Set expiration to "No expiration" (or a long time)
    4. Tick the "repo" checkbox (full repo access)
    5. Click "Generate token" at the bottom
    6. Copy the token — you won't see it again!

  When the terminal asks for your password, paste the token.
  (It won't look like anything is being typed — that's normal.)

  After this succeeds, your files are on GitHub. config.ini
  and rx_scores.db are in .gitignore so they are NOT uploaded.


  STEP 7 — Enable GitHub Pages
  ─────────────────────────────────────────────────────────
  1. On your repo page, click the "Settings" tab
     (top of the page, not your account settings)
  2. In the left sidebar, click "Pages"
  3. Under "Build and deployment":
       Source:  Deploy from a branch
       Branch:  main    /docs
  4. Click Save

  GitHub will show a message: "Your site is being published."
  The URL will appear there — it looks like:
    https://yourusername.github.io/osu-darx-scores


  STEP 8 — Publish your showcase
  ─────────────────────────────────────────────────────────
  Back in your terminal, run:

    python rx_publish.py

  This generates your showcase page, commits it to the docs/
  folder, and pushes it to GitHub automatically.

  Wait about 30 seconds, then open your GitHub Pages URL.
  Your top plays are now live for anyone to see!


  UPDATING THE PAGE LATER
  ─────────────────────────────────────────────────────────
  Any time you want to refresh the page with new scores,
  just run:

    python rx_publish.py

  That's the only command you need from now on.

  Options:
    python rx_publish.py --filter ranked        (ranked only)
    python rx_publish.py --filter ranked+loved  (ranked + loved)
    python rx_publish.py --limit 100            (show top 100)


  TROUBLESHOOTING
  ─────────────────────────────────────────────────────────
  "git is not recognized"
    → Git isn't installed or not on your PATH.
      Reinstall from https://git-scm.com and open a fresh terminal.

  "remote: Repository not found"
    → Check the URL you used in Step 5 matches your GitHub repo exactly.

  "src refspec main does not match any"
    → Run:  git add .  then  git commit -m "init"  before pushing.

  "Permission denied" / authentication failed
    → Make sure you're using a Personal Access Token (Step 6),
      not your GitHub account password.

  Page shows "404" after pushing
    → Wait a minute and refresh. Also double-check Settings → Pages
      is set to "main" branch and "/docs" folder.

  Page is outdated after running rx_publish.py
    → GitHub Pages can take up to 60 seconds to rebuild.
      Hard-refresh your browser with Ctrl+Shift+R.