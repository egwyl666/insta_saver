# dl_bot — Telegram bot for downloading from Instagram and Twitter/X

Whitelist, a pool of throwaway Instagram accounts, file_id cache, admin panel on port 8082.

Done: **stage 1** (database and storage layer) and **stage 2** (bot core).
Coming up: waiting queue for follow requests (stage 3) and the admin panel (stage 4).

## Features

- Picks up Instagram and Twitter links in any message, including several at once
- Understands mirrors (vxtwitter, ddinstagram, etc.) and strips junk parameters
- Instagram: posts, reels, stories, highlights — via gallery-dl, with yt-dlp as a fallback
- Twitter: videos via yt-dlp, images via gallery-dl, no login required
- Carousels are sent as albums, split into chunks of 10 files (Telegram's limit)
- Cache: a repeated link is served instantly by `file_id`, without downloading again
- Account pool: load is spread across accounts, with cooldowns; an expired account drops out on its own and the task moves to the next one
- Alerts to the owner via DM, with throttling
- `/queue`, `/cancel`, admin commands `/allow`, `/ban`, `/stats`

## Installation

One line — clones the repo into `~/dl_bot`, installs ffmpeg and the Python
dependencies, then `--setup` creates `.env`, generates `COOKIES_KEY`, asks for
the bot token and your Telegram ID, and creates the database.

Linux / Raspberry Pi:

```bash
sudo apt-get install -y git python3-venv ffmpeg && git clone -b claude/nice-sagan-eoqy9o https://github.com/egwyl666/insta_saver.git ~/dl_bot && cd ~/dl_bot && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m db.init_db --setup
```

Windows (PowerShell, needs Python 3.10+ and winget):

```powershell
winget install -e --id Git.Git; winget install -e --id Gyan.FFmpeg; git clone -b claude/nice-sagan-eoqy9o https://github.com/egwyl666/insta_saver.git $HOME\dl_bot; cd $HOME\dl_bot; py -m venv .venv; .venv\Scripts\pip install -r requirements.txt; .venv\Scripts\python -m db.init_db --setup
```

`--setup` is safe to re-run: it keeps what is already filled in and only asks for what is missing.

Run manually: `.venv/bin/python -m bot.main` (Windows: `.venv\Scripts\python -m bot.main`)

ffmpeg is needed by yt-dlp to merge separate video/audio tracks (reels, Twitter videos).

Deploy on a Pi: `bash deploy/install.sh` — fills in REPLACE_USER/REPLACE_HOME,
installs the systemd units and enables weekly updates of yt-dlp and gallery-dl.

## Cookies

Until the admin panel exists, accounts are added by hand:

```python
python - <<'PY'
from accounts import crypto
from db import storage
rel = crypto.store("acc_one", open("cookies.txt","rb").read())
print(storage.add_account("acc_one", rel))
PY
```

Export `cookies.txt` (Netscape format) with a browser extension from a browser
logged in to a **throwaway** account. Not your main one.

## Testing

```bash
python smoke_test.py        # database, links, encryption
python smoke_test_bot.py    # bot core against a fake Telegram, error classification
```

Both run on a temporary database without network access and never touch the production one.
GitHub Actions runs them on every push.

## Files

| File | Responsibility |
|---|---|
| `db/schema.sql`, `db/storage.py` | schema and all database access |
| `common/urls.py` | link normalization, mirrors, media type |
| `accounts/crypto.py` | cookie encryption |
| `downloaders/base.py` | running tools, error classification, collecting files |
| `downloaders/instagram.py`, `twitter.py` | download strategies |
| `bot/queue.py` | workers, full task lifecycle, retries |
| `bot/sender.py` | sending, media groups, file_id cache |
| `bot/handlers.py` | commands and link intake |
| `bot/texts.py` | all bot texts in one place |
| `bot/alerts.py` | owner alerts |
| `deploy/` | systemd units + install.sh |
