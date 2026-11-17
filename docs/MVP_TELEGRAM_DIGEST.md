# MVP: Daily Telegram Digest

Run one command daily. It scrapes Hamburg real estate listings for each search
in your config, dedups against already-posted deals, and posts the top N
per search to a Telegram channel.

No bot to run continuously. No database server. No web UI. Just cron + a
Python script + a YAML file.

## What you get

Each morning, your channel receives a header per search followed by one
message per deal with:
- District, rooms, size
- Price, price/m², discount vs market
- Score (0-100), gross rental yield, monthly cashflow
- Red flags (Erbbaurecht, WBS, Dachgeschoss, Ausbau, energy class F/G/H)
- Two strongest opportunity signals
- A link to the original listing

## One-time setup (10 minutes)

### 1. Create a Telegram bot
1. Open Telegram, search for `@BotFather`
2. Send `/newbot`, follow the prompts, pick a name like `HamburgDealsBot`
3. Save the token it gives you (looks like `1234567890:ABC-DEF...`)

### 2. Create a Telegram channel
1. In Telegram: menu → New Channel → name it (e.g. `Hamburg Deals Demo`)
2. Set it public and pick a username (e.g. `@HamburgDealsDemo`)
3. Add your bot as **administrator** of the channel with "Post Messages" permission
4. Subscribe to the channel yourself so you see the messages

### 3. Clone and install
```bash
git clone https://github.com/bobovnii/hello-world.git
cd hello-world
git checkout claude/real-estate-deal-scraper-Vbj9C
pip install -r requirements.txt
```

### 4. Configure searches
```bash
cp config/searches.example.yaml config/searches.yaml
# Edit config/searches.yaml to match what you want to monitor
$EDITOR config/searches.yaml
```

Each search is a named criteria set: budget range, rooms, size, districts,
property type, financing assumptions. See the example file for all fields.

### 5. Set the bot token
```bash
export TELEGRAM_BOT_TOKEN="1234567890:ABC-DEF..."
```

Or put it in a `.env` file (already gitignored):
```
TELEGRAM_BOT_TOKEN=1234567890:ABC-DEF...
```
and load it before running: `set -a; source .env; set +a`.

### 6. Dry run — verify without posting
```bash
python scripts/send_telegram_digest.py --dry-run -v
```

You'll see each message the script WOULD send, plus a final summary line like:
```
digest_complete searches_ok=3/3 messages_sent=0 channel=@HamburgDealsDemo dry_run=True
```

### 7. Live run — post for real
```bash
python scripts/send_telegram_digest.py -v
```

Check your channel. First run posts up to `max_deals_per_search` historical
matches per search. Subsequent runs only post newly-seen listings.

## Automating daily runs

### Option A: cron on a VPS or your Mac

```bash
crontab -e
# Add:
0 8 * * * cd /path/to/hello-world && TELEGRAM_BOT_TOKEN=... /path/to/python scripts/send_telegram_digest.py >> /var/log/hamburg_deals.log 2>&1
```

On a Mac, cron works but `launchd` is cleaner. For a demo, cron is fine.

### Option B: GitHub Actions (free, runs in the cloud)

Commit `.github/workflows/digest.yml`:
```yaml
name: Daily Deal Digest
on:
  schedule:
    - cron: '0 7 * * *'  # 08:00 CET
  workflow_dispatch:  # allow manual run from the GitHub UI

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python scripts/send_telegram_digest.py -v
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
```

1. Add `TELEGRAM_BOT_TOKEN` as a repo secret (Settings → Secrets and variables → Actions)
2. Commit and push
3. The workflow runs daily automatically

**Gotcha**: GitHub Actions uses ephemeral runners, so the SQLite DB that
tracks seen listings does NOT persist across runs. For the MVP demo this
means every morning you'll get the full batch again. Options:
- Accept it (fine for proving the concept)
- Commit the DB back to the repo after each run (ugly, but works)
- Move to a real host (see Option C)

### Option C: Hetzner CX11 (€4.15/mo, runs forever)

```bash
# On your VPS
git clone https://github.com/bobovnii/hello-world.git
cd hello-world
git checkout claude/real-estate-deal-scraper-Vbj9C
pip install -r requirements.txt
cp config/searches.example.yaml config/searches.yaml
$EDITOR config/searches.yaml

echo 'TELEGRAM_BOT_TOKEN=...' > .env

crontab -e
# Add:
0 8 * * * cd /home/you/hello-world && set -a && source .env && set +a && /usr/bin/python3 scripts/send_telegram_digest.py >> /home/you/digest.log 2>&1
```

Done. DB persists on the VPS, new deals de-dup correctly.

## Dedup behavior

The script tracks posted listings in a local SQLite table `seen_deals`,
keyed by `(channel, listing_id)`. A listing is only posted once per channel.

To re-post everything (e.g. after rotating criteria dramatically):
```bash
sqlite3 data/deals.db "DELETE FROM seen_deals WHERE channel = '@YourChannel'"
```

## Troubleshooting

**`config.telegram.channel is required`** — you're pointing at
`config/searches.yaml` but it doesn't exist or has no `telegram.channel`
field. Check `--config` path.

**`Environment variable TELEGRAM_BOT_TOKEN is empty`** — `export` the token
or put it in `.env` and `source` before running.

**Bot posts fail with "chat not found"** — bot isn't an admin of the channel,
or channel username is wrong. Double-check both.

**Bot posts fail with "Forbidden: bot can't send messages to user"** — for
a channel handle (`@foo`), the bot must be an admin. For a numeric chat_id
of a user, the user must have started a chat with the bot first (`/start`).

**All platforms return 0 for one search** — your criteria might be too
narrow (e.g. a district the scrapers don't find under your budget). Dry-run
with wider criteria to confirm the pipeline works, then tighten back.

**Searches take >5 minutes** — reduce `--max-pages 2` or `--max-pages 1`.
Each page per scraper is ~10 seconds; 4 scrapers × 3 pages × 3 searches ≈
4 minutes, dominated by ImmoScout's mobile API.

## What this MVP does NOT do

- No interactive commands (no `/search`, `/criteria` etc.). Criteria are
  only set by editing the YAML. This is intentional: simpler = shippable.
- No per-user personalization. Everyone who subscribes to the channel sees
  the same digest. For personalization, see
  `docs/FLAVOR_B_DESIGN.md` (the full bot design, deferred post-MVP).
- No payment / auth. The channel is either public or private; whoever is
  invited sees everything.
