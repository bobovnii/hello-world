# Test 4: Market Data & Investment Estimates
**Date**: 2026-04-06 20:18

## Request
```
Tool: get_market_data(districts=None)  # all Hamburg
Tool: estimate_investment(200k, 60m², per district)
```

## Hamburg Districts Overview

| District | Avg Price/m² | Avg Rent/m² | Yield | Trend |
|----------|:-----------:|:-----------:|:-----:|:-----:|
| Altona | EUR 5,800 | EUR 14.50 | 3.0% | rising |
| Eimsbüttel | EUR 6,200 | EUR 15.00 | 2.9% | stable |
| Hamburg-Mitte | EUR 5,500 | EUR 14.00 | 3.1% | stable |
| Hamburg-Nord | EUR 5,000 | EUR 13.00 | 3.1% | rising |
| Wandsbek | EUR 4,200 | EUR 11.50 | 3.3% | rising |
| Bergedorf | EUR 3,500 | EUR 10.50 | 3.6% | stable |
| Harburg | EUR 3,200 | EUR 10.00 | 3.8% | rising |

**Purchase costs**: 11.07% (grunderwerbsteuer: 5.5%, notar: 1.5%, grundbuch: 0.5%, makler: 3.57%)

## Investment Estimate: EUR 200k apartment (60m²) per district

| District | Rent/mo | Mortgage/mo | Cashflow/mo | Yield | vs Market |
|----------|:-------:|:-----------:|:-----------:|:-----:|:---------:|
| Altona | EUR 870 | EUR 890 | EUR -212 | 5.2% | -42% |
| Eimsbüttel | EUR 900 | EUR 890 | EUR -183 | 5.4% | -46% |
| Hamburg-Mitte | EUR 840 | EUR 890 | EUR -242 | 5.0% | -39% |
| Hamburg-Nord | EUR 780 | EUR 890 | EUR -300 | 4.7% | -33% |
| Wandsbek | EUR 690 | EUR 890 | EUR -387 | 4.1% | -21% |
| Bergedorf | EUR 630 | EUR 890 | EUR -445 | 3.8% | -5% |
| Harburg | EUR 600 | EUR 890 | EUR -474 | 3.6% | +4% |