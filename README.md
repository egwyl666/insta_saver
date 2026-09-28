# dl_bot — Telegram bot for downloading from Instagram and Twitter/X

Whitelist, a pool of throwaway Instagram accounts, file_id cache, web admin on port 9000.

Done: **stage 1** (database and storage layer), **stage 2** (bot core) and **stage 4** (web admin).
Coming up: waiting queue for follow requests (stage 3).

## Features

- Picks up Instagram and Twitter links in any message, including several at once
- Understands mirrors (vxtwitter, ddinstagram, etc.) and strips junk parameters
- Instagram: posts, reels, stories, highlights — via gallery-dl, with yt-dlp as a fallback
- Instagram without an account first: public posts and reels are fetched anonymously;
  an account from the pool is used only when that fails (private profiles, 18+, stories).
  After an anonymous rate limit the bot goes straight to accounts for 30 minutes.
  Setting `instagram_anon_first` turns this off
- Twitter: videos via yt-dlp, images via gallery-dl, no login required
- Carousels are sent as albums, split into chunks of 10 files (Telegram's limit)
- Cache: a repeated link is served instantly by `file_id`, without downloading again
- Account pool: load is spread across accounts, with cooldowns; an expired account drops out on its own and the task moves to the next one
- Several owners: every admin gets alerts via DM (throttled) and admin commands
- `/queue`, `/cancel`, admin commands `/allow`, `/ban`, `/stats`, `/owner`, `/unowner`
- Web admin: accounts (upload cookies), users and owners, tasks, settings, event log

## Installation

One line — clones the repo into `~/dl_bot`, installs ffmpeg and the Python
dependencies, then `--setup` creates `.env`, generates `COOKIES_KEY`, asks for
the bot token, your Telegram ID and a password for the web admin, and creates the database.

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

## Autostart (systemd)

```bash
cd ~/dl_bot && bash deploy/install.sh
```

Installs and starts the bot and the web admin as services that come back after a reboot
or a crash, and enables a weekly update of yt-dlp and gallery-dl. It stops copies started
by hand first (two bots with one token fight over updates). Re-run it after `git pull`
to restart the services on the new code.

```bash
systemctl status dl_bot dl_bot_web        # are they running
journalctl -u dl_bot -f                   # bot log (web: -u dl_bot_web)
sudo systemctl restart dl_bot dl_bot_web  # restart
sudo systemctl disable --now dl_bot dl_bot_web dl_bot_update.timer   # remove from autostart
```

## Web admin

```bash
.venv/bin/python -m db.init_db --web-password   # once, if --setup skipped it
.venv/bin/python -m web.app                     # http://<pi-ip>:9000
```

Log in as `WEB_USER` (default `admin`) with that password. The port is `WEB_PORT` in `.env`.
`WEB_HOST=0.0.0.0` makes it reachable from the local network, `127.0.0.1` only from the Pi itself.
The admin runs as its own process and shares only the database with the bot, so changes
apply immediately without restarting the bot.

It is plain HTTP: keep the port inside your home network or behind a VPN (Tailscale,
WireGuard) — do not forward it to the internet. After 5 wrong passwords the IP is locked out for 5 minutes.

Sections: summary, accounts (upload/refresh cookies, disable, delete), users and owners,
tasks (filter, cancel), settings, event log. The database is backed up to `data/backups`
before settings changes and account deletions.

## Owners

Owners are all admins: each gets alerts and admin commands. `ADMIN_TG_ID` in `.env` is only
the first owner. To add or replace an owner, pick one:

- in the bot: `/owner 987654321` to add, `/unowner 123456789` to demote, `/owner` to list;
- in the web admin, Users: "Сделать владельцем" / "Понизить";
- in the terminal: `.venv/bin/python -m db.init_db --add-owner 987654321` / `--remove-owner 123456789`.

To hand the bot over: add the new owner first, then demote the old one. The last owner cannot be
demoted or banned. A demoted owner keeps normal access to the bot (ban them to remove it).
The Telegram ID of any account can be looked up with @userinfobot.

## Cookies

Public Instagram posts and Twitter work without an account. An Instagram account
is needed for private profiles (the account must follow them), 18+ content and stories.

1. In a desktop browser, log in to Instagram with a **throwaway** account (not your main one).
2. Export cookies for instagram.com in Netscape format with an extension such as
   "Get cookies.txt LOCALLY" (Chrome) or "cookies.txt" (Firefox).
3. Copy the file to the bot machine and add it to the pool:

```bash
.venv/bin/python -m accounts.add cookies.txt --label acc_one
.venv/bin/python -m accounts.add --list      # what is in the pool
rm cookies.txt                               # the pool keeps an encrypted copy
```

No bot restart needed. When an account drops out (the bot alerts you), export fresh
cookies and run the same command with the same `--label` — it goes back into the pool.

## Testing

```bash
python smoke_test.py        # database, links, encryption
python smoke_test_bot.py    # bot core against a fake Telegram, error classification
python smoke_test_web.py    # web admin: login, CSRF, accounts, owners, settings
```

All run on a temporary database without network access and never touch the production one.
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
| `bot/alerts.py` | alerts to all owners |
| `accounts/pool.py`, `accounts/add.py` | adding accounts (shared by web and CLI) |
| `web/app.py`, `web/templates/` | web admin |
| `db/init_db.py` | setup, owners, web password |
| `deploy/` | systemd units + install.sh |
