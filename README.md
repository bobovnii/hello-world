# Hamburg Real Estate Deal Finder

Scrapes Hamburg real estate listings from ImmoScout24, Kleinanzeigen.de, and Immowelt, analyzes them for investment potential, and identifies undervalued deals with good ROI.

## Features

- **Multi-platform scraping**: ImmoScout24, Kleinanzeigen.de, Immowelt
- **Investment analysis**: Price/m2, rental yield, cap rate, cashflow, cash-on-cash return
- **Undervalue detection**: Estate sales, motivated sellers, below-market pricing, renovation opportunities
- **Deal scoring**: Composite 0-100 score based on configurable weights
- **Hamburg market data**: District-level averages for price, rent, and trends (Mietspiegel)
- **Interactive CLI**: Rich terminal interface with tables and drill-down
- **Telegram bot**: Set criteria, search on demand, daily deal alerts
- **Export**: CSV and JSON export

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Interactive CLI mode
python main.py cli

# Batch scrape with export
python main.py scrape --max-price 300000 --export csv

# Telegram bot (set token first)
export TELEGRAM_BOT_TOKEN="your-token-here"
python main.py telegram
```

## How It Works

1. **Set criteria**: Budget, size, rooms, districts, financing params, risk tolerance
2. **Scrape**: Parallel scraping of 3 platforms with rate limiting and rotating user agents
3. **Analyze**: Calculate investment metrics using Hamburg Mietspiegel rental data
4. **Score**: Rank deals 0-100 based on price discount, yield, cashflow, and undervalue signals
5. **Present**: Top deals with color-coded metrics and drill-down details

## Scoring Algorithm

Each deal is scored 0-100 based on weighted factors:

| Factor | Conservative | Moderate | Aggressive |
|--------|-------------|----------|------------|
| Price below market | 20% | 25% | 30% |
| Rental yield | 15% | 25% | 20% |
| Monthly cashflow | 35% | 20% | 10% |
| Undervalue signals | 10% | 15% | 25% |
| Location trend | 20% | 15% | 15% |

## Undervalue Signals Detected

- Below-market price per m2 (vs district average)
- High rental yield vs district average
- Estate/inheritance sale keywords (Nachlass, Erbe)
- Urgent sale signals (schneller Verkauf, VB)
- Renovation opportunity (renovierungsbedurftig)
- Foreclosure indicators (Zwangsversteigerung)
- Long time on market (>60 days)
- Price below implied value from rental potential

## Hamburg Districts Covered

| District | Avg Price/m2 | Avg Rent/m2 | Trend |
|----------|-------------|-------------|-------|
| Altona | 5,800 EUR | 14.50 EUR | Rising |
| Eimsbuttel | 6,200 EUR | 15.00 EUR | Stable |
| Hamburg-Mitte | 5,500 EUR | 14.00 EUR | Stable |
| Hamburg-Nord | 5,000 EUR | 13.00 EUR | Rising |
| Wandsbek | 4,200 EUR | 11.50 EUR | Rising |
| Bergedorf | 3,500 EUR | 10.50 EUR | Stable |
| Harburg | 3,200 EUR | 10.00 EUR | Rising |

## Project Structure

```
src/
  scraper/     - Platform scrapers (ImmoScout, Kleinanzeigen, Immowelt)
  analyzer/    - Metrics, scoring, undervalue detection, market data
  database/    - SQLite models and CRUD operations
  bot/         - CLI (Rich) and Telegram bot interfaces
data/          - Market reference data, SQLite DB, exports
tests/         - Unit tests
config.yaml    - Scraper settings, scoring weights, thresholds
main.py        - Entry point
```

## Disclaimer

This tool is for personal research purposes. Scraping may violate the Terms of Service of the platforms. Use responsibly, respect rate limits, and check robots.txt. Market data is approximate and should be verified against current sources.
