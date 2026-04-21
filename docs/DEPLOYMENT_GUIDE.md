# Hamburg Real Estate Deal Finder - Deployment Guide

## Phase 1: Immediate Deployment (Zero New Code)

### 1.1 Claude Desktop (MCP Server)

**What it does:** Any Claude Desktop user can chat naturally with the agent to search Hamburg deals, analyze listings, compare districts, and get investment estimates.

**Setup (2 minutes):**

1. Clone the repo:
```bash
git clone https://github.com/bobovnii/hello-world.git
cd hello-world
git checkout claude/real-estate-deal-scraper-Vbj9C
pip install -r requirements.txt
pip install "mcp[cli]" aiosqlite
```

2. Add to Claude Desktop config:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "hamburg-deals": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/hello-world"
    }
  }
}
```

3. Restart Claude Desktop. A hammer icon appears in the chat input.

**Example conversations:**
- "Find me 2-room apartments near DESY under 250k"
- "What's the average price per sqm in Eimsbüttel?"
- "Analyze this listing: https://www.immobilienscout24.de/expose/166737014"
- "Compare Harburg vs Wandsbek for investment"
- "What would a 200k, 60sqm apartment in Altona yield?"

---

### 1.2 Claude Code (CLI + Web)

**Setup (30 seconds):**
```bash
cd hello-world
claude mcp add hamburg-deals -- python -m mcp_server.server
```

Now in any Claude Code session in this repo, the 7 tools are available.

**HTTP mode (for remote access / MCP Inspector):**
```bash
python -m mcp_server.server --http
# Serves at http://localhost:8000/mcp

# Debug with MCP Inspector:
npx @modelcontextprotocol/inspector
# Connect to http://localhost:8000/mcp
```

---

### 1.3 Daily Deal Digest (Cron Job)

**What it does:** Runs every morning at 7:00 AM, scrapes all platforms, scores deals, saves a markdown report.

**Setup:**

Create `scripts/daily_digest.py`:
```python
#!/usr/bin/env python3
"""Daily deal digest - run via cron."""
import sys, os, logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.WARNING)

from concurrent.futures import ThreadPoolExecutor, as_completed
from src.database.models import UserCriteria
from src.database.db import Database
from src.scraper.immoscout import ImmoScoutScraper
from src.scraper.kleinanzeigen import KleinanzeigenScraper
from src.scraper.immowelt import ImmoweltScraper
from src.scraper.ohne_makler import OhneMaklerScraper
from src.analyzer.market_data import HamburgMarketData
from src.analyzer.scorer import DealScorer

# Configure your search criteria here
SEARCHES = [
    {
        "name": "2-Room DESY <250k",
        "criteria": UserCriteria(
            budget_max=250000, min_rooms=2, min_size_sqm=30,
            property_types=["apartment"], districts=["Altona", "Eimsbüttel"],
        ),
    },
    {
        "name": "3-Room DESY <300k",
        "criteria": UserCriteria(
            budget_max=300000, min_rooms=3, min_size_sqm=50,
            property_types=["apartment"], districts=["Altona", "Eimsbüttel"],
        ),
    },
    {
        "name": "MFH Hamburg <1.5M",
        "criteria": UserCriteria(
            budget_min=200000, budget_max=1500000, min_size_sqm=80,
            property_types=["multi_family"],
        ),
    },
]

def run():
    market = HamburgMarketData()
    scorer = DealScorer(market)
    db = Database()
    date = datetime.now().strftime("%Y-%m-%d")
    report = [f"# Daily Deal Digest - {date}\n"]

    for search in SEARCHES:
        scrapers = [
            ImmoScoutScraper(rate_limit_min=2, rate_limit_max=4),
            KleinanzeigenScraper(rate_limit_min=2, rate_limit_max=4),
            ImmoweltScraper(rate_limit_min=2, rate_limit_max=4),
            OhneMaklerScraper(rate_limit_min=2, rate_limit_max=4),
        ]
        all_listings = []
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {ex.submit(s.search, search["criteria"], 3): s for s in scrapers}
            for f in as_completed(futures):
                try:
                    all_listings.extend(f.result(timeout=180))
                except Exception:
                    pass
                finally:
                    futures[f].close()

        results = scorer.score_and_rank(all_listings, search["criteria"])
        db.save_listings(all_listings)
        for _, a in results:
            db.save_analysis(a)

        report.append(f"## {search['name']} ({len(results)} deals)\n")
        for i, (l, a) in enumerate(results[:5], 1):
            flags = []
            if l.is_erbbaurecht: flags.append("ERBBAU")
            if l.is_rented: flags.append("RENTED")
            if l.is_ausbau_needed: flags.append("AUSBAU")
            if l.energy_rating and l.energy_rating.upper() in ("F","G","H"): flags.append(f"E:{l.energy_rating}")
            flag_str = f" | {' '.join(flags)}" if flags else ""
            report.append(
                f"{i}. **[{a.deal_score:.0f}]** EUR {l.price:,.0f} | "
                f"{l.size_sqm:.0f}m² | {l.rooms:.0f}rm | {l.district}"
                f"{flag_str}\n"
                f"   {l.title[:60]}\n"
                f"   {l.url}\n"
            )
        report.append("---\n")

    # Save report
    out_dir = Path("reports")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"digest_{date}.md"
    path.write_text("\n".join(report))
    print(f"Saved: {path}")

if __name__ == "__main__":
    run()
```

**Cron setup (Linux/Mac):**
```bash
crontab -e
# Add: run daily at 7:00 AM
0 7 * * * cd /path/to/hello-world && python scripts/daily_digest.py
```

**GitHub Actions (free, runs in cloud):**
```yaml
# .github/workflows/daily_digest.yml
name: Daily Deal Digest
on:
  schedule:
    - cron: '0 7 * * *'  # 7 AM UTC daily
  workflow_dispatch:  # manual trigger

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: claude/real-estate-deal-scraper-Vbj9C
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python scripts/daily_digest.py
      - uses: actions/upload-artifact@v4
        with:
          name: deal-digest
          path: reports/
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                    USER INTERFACES                   │
├──────────┬──────────┬──────────┬────────────────────┤
│  Claude  │  Claude  │  Daily   │     Telegram       │
│  Desktop │   Code   │  Digest  │     Bot (P2)       │
│  (MCP)   │  (MCP)   │  (cron)  │                    │
└────┬─────┴────┬─────┴────┬─────┴────────┬───────────┘
     │          │          │              │
     └──────────┴──────┬───┘              │
                       │                  │
              ┌────────▼────────┐  ┌──────▼──────┐
              │   MCP Server    │  │ Claude API  │
              │   (7 tools)     │  │ + Agent SDK │
              └────────┬────────┘  └──────┬──────┘
                       │                  │
              ┌────────▼──────────────────▼──────┐
              │          Core Engine              │
              ├──────────┬───────────┬───────────┤
              │ Scrapers │ Analyzer  │ Database  │
              │ (4 plat) │ (scoring) │ (SQLite)  │
              └──────────┴───────────┴───────────┘
```

---

## What's Available Now (Phase 1)

| Component | Status | Notes |
|-----------|:------:|-------|
| MCP Server (7 tools) | Ready | `python -m mcp_server.server` |
| ImmoScout24 scraper | Working | Mobile API, 25-40 listings/search |
| Kleinanzeigen scraper | Working | HTML, 5-10 listings/search |
| Immowelt scraper | Partial | Search cards only (detail blocked by DataDome) |
| Ohne-Makler scraper | Working | Full detail pages, provisionsfrei |
| Financial analysis | Working | Yields, cashflow, cap rate, scoring |
| Red flag detection | Working | Erbbaurecht, WBS, Ausbau, Energy, etc. |
| District filtering | Working | All 7 Bezirke, verified correct |
| CLI bot | Working | `python main.py cli` |
| Telegram bot | Built | Needs TELEGRAM_BOT_TOKEN to deploy |
| Daily digest script | Ready | Needs cron setup |
| 63 unit tests | Passing | `pytest tests/` |
| 8 integration tests | Passing | `python tests/run_mcp_integration.py` |

---

## Phase 2: Telegram Bot + Daily Alerts (Week 2)

**What to build:**
- Deploy Telegram bot on Railway ($5/mo)
- Connect to Claude Agent SDK as backend
- Add daily digest push via bot
- Inline keyboards for criteria selection

**Estimated effort:** 3-5 days

---

## Phase 3: Web App (Month 2)

**Options:**
- **Chainlit** (fastest, Python): production chat UI in ~100 lines
- **Next.js + Vercel AI SDK** (most polished): 1-2 weeks

**Estimated effort:** 1-2 weeks

---

## Phase 4: Scale (Month 3+)

- Immowelt SOAP API integration (email api@immowelt.de for free key)
- ImmoScout24 official API (apply at api.immobilienscout24.de)
- n8n automation workflows
- Slack integration for teams
- Multi-agent architecture if needed

---

## Immediate Next Steps

1. **Test Claude Desktop:** Add MCP config, restart, try the example conversations
2. **Test Claude Code:** Run `claude mcp add hamburg-deals -- python -m mcp_server.server`
3. **Set up daily digest:** Create `scripts/daily_digest.py` and configure cron
4. **Email Immowelt:** Request free API key from api@immowelt.de
5. **Get Telegram bot token:** Message @BotFather on Telegram, set TELEGRAM_BOT_TOKEN
