# Test 5: Aggressive Renovation Deals
**Date**: 2026-03-31T08:19:18

## Request
```
Tool: start_search + get_search_results
budget_max: 200,000 EUR
budget_min: 50,000 EUR
min_rooms: 1
min_size_sqm: 25
property_type: apartment
districts: all Hamburg
equity_pct: 15%
interest_rate: 4.0%
risk_tolerance: aggressive
```

## Search Metadata
- **Platforms searched**: immoscout, kleinanzeigen, immowelt, ohne-makler
- **Platforms failed**: none
- **Total listings found**: 6
- **Duration**: 61s
- **Deals scored**: 5

## Results
### Deal #1
- **Title**: Bramfeld, Hamburg (22175)
- **URL**: https://www.immowelt.de/expose/7c6d5307-6f0b-443c-893f-5cf82dce494d
- **Platform**: immowelt
- **Price**: EUR 210,000
- **Size**: 89.0 m² | **Rooms**: 2.0
- **District**: Hamburg
- **Address**: Bramfeld, Hamburg (22175)
- **Property type**: apartment
- **Deal Score**: 80.4/100
- **Price/m²**: EUR 2,360 (-57.1% vs market)
- **Gross Yield**: 7.12%
- **Monthly Cashflow**: EUR -13
- **Undervalue Signals**:
  - Price 57% below district average (€2360/m² vs €5500/m²)
  - Rental yield 7.1% (+4.0% above district avg 3.1%)
  - Price significantly below implied value (€210,000 vs est. €498,400)

### Deal #2
- **Title**: Freie Wohnung mit Potenzial – 2 Zimmer Wohnung für Handwerker
- **URL**: https://www.ohne-makler.net/immobilie/440922/
- **Platform**: ohne-makler
- **Price**: EUR 179,000
- **Size**: 43.0 m² | **Rooms**: 2.0
- **District**: Hamburg
- **Address**: 22525 Hamburg
							
								(Stellingen)
- **Property type**: apartment
- **Deal Score**: 63.1/100
- **Price/m²**: EUR 4,163 (-24.3% vs market)
- **Gross Yield**: 4.04%
- **Monthly Cashflow**: EUR -818
- **Description**: •Praktischer Flur•Helles Wohn- und Esszimmer mit Zugang zum Balkon•Schlafzimmer•Separa­te Küche mit Fenster und Abstellmöglichkeit•Wannenbad­
- **Undervalue Signals**:
  - Price 24% below district average (€4163/m² vs €5500/m²)
  - Renovation needed - potential value-add after improvements
  - Price significantly below implied value (€179,000 vs est. €240,800)

### Deal #3
- **Title**: Wandsbeker Stieg 28, Hohenfelde, Hamburg (22087)
- **URL**: https://www.immowelt.de/expose/ba390a86-24ee-4117-b0a5-d32601ae47eb
- **Platform**: immowelt
- **Price**: EUR 170,000
- **Size**: 33.9 m² | **Rooms**: 1.0
- **District**: Hamburg-Nord
- **Address**: Wandsbeker Stieg 28, Hohenfelde, Hamburg (22087)
- **Property type**: apartment
- **Deal Score**: 29.8/100
- **Price/m²**: EUR 5,015 (+0.3% vs market)
- **Gross Yield**: 3.11%
- **Monthly Cashflow**: EUR -561

### Deal #4
- **Title**: Couratgefrei: Sanierte Dachgeschoss-Wohnung mit Südwest-Loggia in Meiendorf
- **URL**: https://www.ohne-makler.net/immobilie/440623/
- **Platform**: ohne-makler
- **Price**: EUR 198,000
- **Size**: 44.16 m² | **Rooms**: 1.5
- **District**: Wandsbek
- **Address**: 22145 Hamburg
							
								(Rahlstedt)
- **Property type**: multi_family
- **Deal Score**: 25.5/100
- **Price/m²**: EUR 4,484 (+6.8% vs market)
- **Gross Yield**: 3.08%
- **Monthly Cashflow**: EUR -897
- **Description**: Zum selber Wohnen oder als ideale Kapitalanlage: die sanierte, lichtdurchflutete 1,5 Zimmer Dachgeschoss-Wohnung bietet modernes Wohnambiente mit viel Charme.Ruhig gelegen und gleichzeitig perfekt ang

### Deal #5
- **Title**: Niendorf, Hamburg Niendorf (22455)
- **URL**: https://www.immowelt.de/expose/a460530d-745c-413e-a829-7a3884d225ac
- **Platform**: immowelt
- **Price**: EUR 205,000
- **Size**: 37.0 m² | **Rooms**: 1.5
- **District**: Hamburg
- **Address**: Niendorf, Hamburg Niendorf (22455)
- **Property type**: apartment
- **Deal Score**: 24.2/100
- **Price/m²**: EUR 5,541 (+0.7% vs market)
- **Gross Yield**: 3.03%
- **Monthly Cashflow**: EUR -690


## Deep Analysis: Top Deal (analyze_listing)
```
Tool: analyze_listing
listing_id: immowelt_7c6d5307-6f0b-443c-893f-5cf82dce494d
equity_pct: 15%
interest_rate_pct: 4.0%
```

### Deal #1
- **Title**: Bramfeld, Hamburg (22175)
- **URL**: https://www.immowelt.de/expose/7c6d5307-6f0b-443c-893f-5cf82dce494d
- **Platform**: immowelt
- **Price**: EUR 210,000
- **Size**: 89.0 m² | **Rooms**: 2.0
- **District**: Hamburg
- **Address**: Bramfeld, Hamburg (22175)
- **Property type**: apartment
- **Deal Score**: 80.4/100
- **Price/m²**: EUR 2,360 (-57.1% vs market)
- **Gross Yield**: 7.12%
- **Monthly Cashflow**: EUR -13
- **Undervalue Signals**:
  - Price 57% below district average (€2360/m² vs €5500/m²)
  - Rental yield 7.1% (+4.0% above district avg 3.1%)
  - Price significantly below implied value (€210,000 vs est. €498,400)

#### Full Financial Analysis
- Total Purchase Cost: EUR 233,247
- Equity Required: EUR 34,987
- Mortgage Payment: EUR 1,046/mo
- Estimated Rent: EUR 1,246/mo
- Net Yield: 5.32%
- Cap Rate: 5.32%
- Cash-on-Cash Return: -0.44%
- District Avg Price/m²: EUR 5,500