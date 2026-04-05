# Test 2: Cheapest Hamburg Apartments (<250k, Aggressive)
**Date**: 2026-04-05 08:11

## Request
```
budget_max: 250,000 | min_rooms: 1 | districts: all
property_type: apartment | risk: aggressive | equity: 15% | rate: 4.0%
```

## Search Metadata
- **Platforms OK**: immoscout (36), ohne-makler (0), immowelt (4), kleinanzeigen (3)
- **Platforms failed**: none
- **Total listings scraped**: 43
- **Deals scored**: 43

## Results

### Deal #1 — Score: 88/100

| Field | Value |
|-------|-------|
| **Title** | Wohnung in Hamburg |
| **URL** | https://www.immowelt.de/expose/7c6d5307-6f0b-443c-893f-5cf82dce494d |
| **Platform** | immowelt |
| **Price** | EUR 210,000 |
| **Size** | 89 m² |
| **Rooms** | 2.0 |
| **District** | Hamburg |
| **Address** | Bramfeld, Hamburg (22175) |
| **Energy rating** | F |

**Financial Metrics:**
- Price/m²: EUR 2,360 (district avg: EUR 5,500) = **-57%**
- Gross Yield: 7.12%
- Monthly Cashflow: EUR -13

**Flags:** `DACHGESCHOSS` `AUSBAU NEEDED` `ENERGY F`

**Opportunities (+):**
- [+] Price 57% below district average (€2360/m² vs €5500/m²)
- [+] Rental yield 7.1% (+4.0% above district avg 3.1%)
- [+] Price below implied value (€210,000 vs est. €498,400)

**Red Flags (!):**
- [!] ATTIC APARTMENT (Dachgeschoss) - sloped ceilings reduce usable space. WoFlV: areas under 1m height don't count, 1-2m count 50%. Check if m² is Wohnfläche (WoFlV) or gross area (DIN 277)
- [!] EXPANSION/BUILDOUT NEEDED (Ausbaureserve) - listed m² likely includes unfinished space. Actual livable area may be 30-50% less. Check: Baugenehmigung obtainable? Cost: 1000-2000 €/m² for attic buildout
- [!] POOR ENERGY RATING (F) - EU requires class E by 2030, class D by 2033. Mandatory insulation/heating upgrades on ownership change (2-year deadline). Budget 20,000-60,000€ for energy renovation

**Description:** Sie suchen ein Projekt mit Substanz, Entwicklungspotenzial und Perspektive? Diese Dachgeschosswohnung bietet genau das: eine seltene Kombination aus sofort nutzbarer Fläche und zusätzlichem Ausbaupot......

---

### Deal #2 — Score: 85/100

| Field | Value |
|-------|-------|
| **Title** | Großzügige Endetage mit Weitblick! |
| **URL** | https://www.immobilienscout24.de/expose/161537754 |
| **Platform** | immoscout |
| **Price** | EUR 209,000 |
| **Size** | 84 m² |
| **Rooms** | 3.0 |
| **District** | Hamburg |
| **Address** | 21217 Seevetal, Seevetal (unvollständige Adresse) |
| **Energy rating** | G |

**Financial Metrics:**
- Price/m²: EUR 2,488 (district avg: EUR 5,500) = **-55%**
- Gross Yield: 6.75%
- Monthly Cashflow: EUR -75

**Flags:** `ENERGY G`

**Opportunities (+):**
- [+] Price 55% below district average (€2488/m² vs €5500/m²)
- [+] Rental yield 6.8% (+3.6% above district avg 3.1%)
- [+] Price below implied value (€209,000 vs est. €470,400)

**Red Flags (!):**
- [!] POOR ENERGY RATING (G) - EU requires class E by 2030, class D by 2033. Mandatory insulation/heating upgrades on ownership change (2-year deadline). Budget 20,000-60,000€ for energy renovation

---

### Deal #3 — Score: 84/100

| Field | Value |
|-------|-------|
| **Title** | Ideale Kapitalanlage - gepflegte 
2,5 Zimmer-Wohnung mit Balkon |
| **URL** | https://www.immobilienscout24.de/expose/160386735 |
| **Platform** | immoscout |
| **Price** | EUR 220,000 |
| **Size** | 78 m² |
| **Rooms** | 2.5 |
| **District** | Wandsbek |
| **Address** | 22145 Hamburg, Rahlstedt (unvollständige Adresse) |
| **Energy rating** | D |

**Financial Metrics:**
- Price/m²: EUR 2,821 (district avg: EUR 4,200) = **-33%**
- Gross Yield: 4.89%
- Monthly Cashflow: EUR -410

**Flags:** `TENANTED`

**Opportunities (+):**
- [+] Price 33% below district average (€2821/m² vs €4200/m²)
- [+] Rental yield 4.9% (+1.6% above district avg 3.3%)
- [+] Below market in rising district (Wandsbek) - appreciation potential
- [+] Price below implied value (€220,000 vs est. €358,800)

**Red Flags (!):**
- [!] TENANTED (vermietet) - typically 20-30% discount vs vacant. Check: Hamburg 10-year Kündigungssperrfrist, rent level vs Mietspiegel

---

### Deal #4 — Score: 83/100

| Field | Value |
|-------|-------|
| **Title** | Ihr neues Zuhause oder eine clevere Investition! |
| **URL** | https://www.kleinanzeigen.de/s-anzeige/ihr-neues-zuhause-oder-eine-clevere-investition-/3204875815-196-9437 |
| **Platform** | kleinanzeigen |
| **Price** | EUR 199,000 |
| **Size** | 60 m² |
| **Rooms** | 2.5 |
| **District** | Eimsbüttel |
| **Address** | 22523 Eimsbüttel - Hamburg Eidelstedt |
| **Year built** | 1965 |
| **Hausgeld** | EUR 360/mo |

**Financial Metrics:**
- Price/m²: EUR 3,317 (district avg: EUR 6,200) = **-46%**
- Gross Yield: 5.43%
- Monthly Cashflow: EUR -645

**Flags:** `ERBBAURECHT`

**Opportunities (+):**
- [+] Price 46% below district average (€3317/m² vs €6200/m²)
- [+] Rental yield 5.4% (+2.5% above district avg 2.9%)
- [+] Price below implied value (€199,000 vs est. €360,000)

**Red Flags (!):**
- [!] ERBBAURECHT (leasehold land) - you don't own the land. Typically 20-40% cheaper. Check: lease expiry date, annual Erbbauzins, renewal terms
- [!] HIGH HAUSGELD: €360/mo (€6.0/m² - above typical 2.50-3.50€/m²). Eats into cashflow. Check what's included and if Sonderumlage is pending

**Description:** Diese großzügige 2,5-Zimmer-Wohnung in Eidelstedt mit ca. 60m² Wohnfläche bietet ein ansprechendes Raumkonzept. Das massiv erbaute Gebäude aus dem Jahr 1965 befindet sich auf einem Erbpachtgrundstück mit Erbaurecht bis 2059, verlängerbar um weitere 99 Jahre.Die Wohnung befindet sich im II. Obergesch...

---

### Deal #5 — Score: 82/100

| Field | Value |
|-------|-------|
| **Title** | Maisonettewohnung mit Garten und Garage auf Erbpachtgrundstück zum Kauf in Iserbrook - Wentzel Dr. |
| **URL** | https://www.immobilienscout24.de/expose/166737014 |
| **Platform** | immoscout |
| **Price** | EUR 215,000 |
| **Size** | 74 m² |
| **Rooms** | 2.0 |
| **District** | Hamburg |
| **Address** | 22589 Hamburg / Iserbrook, Iserbrook (unvollständige Adresse) |
| **Energy rating** | D |

**Financial Metrics:**
- Price/m²: EUR 2,905 (district avg: EUR 5,500) = **-47%**
- Gross Yield: 5.78%
- Monthly Cashflow: EUR -246

**Flags:** `ERBBAURECHT`

**Opportunities (+):**
- [+] Price 47% below district average (€2905/m² vs €5500/m²)
- [+] Rental yield 5.8% (+2.7% above district avg 3.1%)
- [+] Price below implied value (€215,000 vs est. €414,400)

**Red Flags (!):**
- [!] ERBBAURECHT (leasehold land) - you don't own the land. Typically 20-40% cheaper. Check: lease expiry date, annual Erbbauzins, renewal terms

---
