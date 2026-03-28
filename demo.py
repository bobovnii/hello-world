#!/usr/bin/env python3
"""Demo: Analyze sample Hamburg-Altona listings near DESY."""

import json
from src.database.models import Listing, UserCriteria
from src.database.db import Database
from src.analyzer.market_data import HamburgMarketData
from src.analyzer.scorer import DealScorer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

# Realistic sample listings near DESY (Bahrenfeld/Altona district)
SAMPLE_LISTINGS = [
    Listing(
        id="demo_1", platform="immoscout", title="2-Zi ETW in Bahrenfeld - Nahe DESY",
        url="https://immobilienscout24.de/expose/demo1",
        price=195000, size_sqm=52, rooms=2, district="Altona", zip_code="22761",
        address="Bahrenfelder Chaussee 45, 22761 Hamburg",
        year_built=1968, hausgeld=220, condition="gepflegt",
        description="Gepflegte 2-Zimmer-Wohnung in ruhiger Seitenstraße nahe DESY. "
        "Balkon, neues Bad. Ideal als Kapitalanlage - aktuell vermietet für 580 EUR/Monat.",
        balcony=True,
    ),
    Listing(
        id="demo_2", platform="kleinanzeigen", title="Renovierungsbedürftige WHG Ottensen - Schnäppchen",
        url="https://kleinanzeigen.de/s-anzeige/demo2",
        price=165000, size_sqm=58, rooms=2.5, district="Altona", zip_code="22765",
        address="Ottenser Hauptstraße 12, 22765 Hamburg",
        year_built=1955, hausgeld=180,
        description="Renovierungsbedürftige Wohnung in Top-Lage Ottensen. Nachlass - schneller Verkauf gewünscht. "
        "Substanz gut, Bad und Küche müssen erneuert werden. Preis VHB.",
        condition="renovierungsbedürftig",
    ),
    Listing(
        id="demo_3", platform="immowelt", title="Moderne 2-Zi Wohnung Lurup",
        url="https://immowelt.de/expose/demo3",
        price=220000, size_sqm=55, rooms=2, district="Altona", zip_code="22761",
        address="Luruper Hauptstraße 88, 22761 Hamburg",
        year_built=2005, hausgeld=280, condition="neuwertig",
        description="Helle, moderne Wohnung mit Einbauküche und Tiefgaragenstellplatz. "
        "Energieeffizient, Fußbodenheizung. 10 Min zu DESY mit dem Fahrrad.",
        parking=True, balcony=True,
    ),
    Listing(
        id="demo_4", platform="immoscout", title="Altbau-Charme in Bahrenfeld",
        url="https://immobilienscout24.de/expose/demo4",
        price=245000, size_sqm=68, rooms=2.5, district="Altona", zip_code="22761",
        address="Bornkampsweg 22, 22761 Hamburg",
        year_built=1910, hausgeld=310, condition="teilsaniert",
        description="Charmante Altbauwohnung mit Stuck und Dielenboden. Teilsaniert, "
        "neue Fenster. Ruhige Lage, 5 Min zum Volkspark. Scheidungsverkauf.",
        balcony=True,
    ),
    Listing(
        id="demo_5", platform="kleinanzeigen", title="Kapitalanlage Bahrenfeld - Vermietet",
        url="https://kleinanzeigen.de/s-anzeige/demo5",
        price=175000, size_sqm=48, rooms=2, district="Altona", zip_code="22761",
        address="Gasstraße 15, 22761 Hamburg",
        year_built=1972, hausgeld=200,
        description="Solide vermietete 2-Zimmer-Wohnung. Aktuelle Miete 620 EUR kalt. "
        "Mieter seit 8 Jahren. Dringend zu verkaufen wegen Umzug ins Ausland. Preis verhandelbar.",
    ),
    Listing(
        id="demo_6", platform="immowelt", title="Großzügige 2-Zi in Altona-Nord",
        url="https://immowelt.de/expose/demo6",
        price=285000, size_sqm=72, rooms=2, district="Altona", zip_code="22769",
        address="Stresemannstraße 200, 22769 Hamburg",
        year_built=1995, hausgeld=350, condition="gepflegt",
        description="Großzügige Wohnung mit offenem Grundriss. S-Bahn Holstenstraße 3 Min. "
        "Kellerraum und Fahrradstellplatz inklusive.",
    ),
    Listing(
        id="demo_7", platform="immoscout", title="Schnäppchen: 2-Zi Lurup Zwangsversteigerung",
        url="https://immobilienscout24.de/expose/demo7",
        price=125000, size_sqm=50, rooms=2, district="Altona", zip_code="22761",
        address="Elbgaustraße 73, 22761 Hamburg",
        year_built=1975, hausgeld=190,
        description="Zwangsversteigerung! 2-Zimmer-Wohnung in Lurup. Verkehrswert lt. Gutachten 170.000 EUR. "
        "Bezugsfrei nach Zuschlag. Grundriss ideal für Kapitalanlage.",
        condition="renovierungsbedürftig",
    ),
    Listing(
        id="demo_8", platform="kleinanzeigen", title="Dachgeschoss-Perle Ottensen",
        url="https://kleinanzeigen.de/s-anzeige/demo8",
        price=259000, size_sqm=62, rooms=2, district="Altona", zip_code="22765",
        address="Rothestraße 8, 22765 Hamburg",
        year_built=1900, hausgeld=240, condition="saniert",
        description="Liebevoll sanierte DG-Wohnung in Ottensen. Exposed Brick, "
        "offene Küche, Dachterrasse. Top Lage am Spritzenplatz.",
        balcony=True,
    ),
]

def main():
    # User criteria: 300k budget, 2 rooms, near DESY (Altona)
    criteria = UserCriteria(
        budget_min=50000,
        budget_max=300000,
        min_size_sqm=40,
        min_rooms=2,
        property_types=["apartment"],
        districts=["Altona"],
        equity_pct=20,
        interest_rate_pct=3.5,
        loan_term_years=25,
        min_gross_yield_pct=3.0,
        min_cashflow_monthly=-200,
        risk_tolerance="moderate",
    )

    console.print(Panel(
        "[bold]Hamburg Deal Finder - DESY / Altona Area[/bold]\n\n"
        f"Budget: 50,000 - 300,000 EUR\n"
        f"Size: 40+ m²  |  Rooms: 2+\n"
        f"District: Altona (Bahrenfeld, Ottensen, Lurup)\n"
        f"Financing: 20% equity, 3.5% rate, 25yr term\n"
        f"Risk: moderate",
        border_style="blue",
    ))

    # Save to DB
    db = Database()
    db.save_listings(SAMPLE_LISTINGS)

    # Score and rank
    market_data = HamburgMarketData()
    scorer = DealScorer(market_data)
    results = scorer.score_and_rank(SAMPLE_LISTINGS, criteria)

    for _, analysis in results:
        db.save_analysis(analysis)

    # Display results
    console.print()
    console.print(Panel(f"[bold green]Top {len(results)} Deals Found[/bold green]", border_style="green"))

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", width=3)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Title", width=38)
    table.add_column("Price", justify="right", width=12)
    table.add_column("Size", justify="right", width=8)
    table.add_column("EUR/m²", justify="right", width=9)
    table.add_column("vs Mkt", justify="right", width=7)
    table.add_column("Yield", justify="right", width=6)
    table.add_column("CF/mo", justify="right", width=8)
    table.add_column("Signals", justify="right", width=3)

    for i, (listing, analysis) in enumerate(results, 1):
        score = analysis.deal_score
        if score >= 70:
            score_str = f"[bold green]{score:.0f}[/bold green]"
        elif score >= 50:
            score_str = f"[yellow]{score:.0f}[/yellow]"
        else:
            score_str = f"[dim]{score:.0f}[/dim]"

        vs_mkt = analysis.price_vs_market_pct
        if vs_mkt < -15:
            mkt_str = f"[bold green]{vs_mkt:+.0f}%[/bold green]"
        elif vs_mkt < 0:
            mkt_str = f"[green]{vs_mkt:+.0f}%[/green]"
        else:
            mkt_str = f"[red]{vs_mkt:+.0f}%[/red]"

        cf = analysis.monthly_cashflow
        cf_str = f"[green]{cf:+,.0f}[/green]" if cf > 0 else f"[red]{cf:+,.0f}[/red]"

        table.add_row(
            str(i), score_str, listing.title[:38],
            f"€{listing.price:,.0f}", f"{listing.size_sqm:.0f} m²",
            f"€{analysis.price_per_sqm:,.0f}", mkt_str,
            f"{analysis.gross_rental_yield_pct:.1f}%", cf_str,
            str(len(analysis.undervalue_reasons)),
        )

    console.print(table)

    # Show details for top 3
    console.print("\n[bold cyan]═══ Detailed Analysis - Top 3 Deals ═══[/bold cyan]")
    for i, (listing, analysis) in enumerate(results[:3], 1):
        console.print(f"\n[bold]#{i} - {listing.title}[/bold]")
        console.print(f"  URL: {listing.url}")
        console.print(f"  Address: {listing.address}")
        console.print(f"  Price: €{listing.price:,.0f}  |  Size: {listing.size_sqm} m²  |  Rooms: {listing.rooms}")
        console.print(f"  Year built: {listing.year_built or 'N/A'}  |  Condition: {listing.condition or 'N/A'}")
        console.print()
        console.print(f"  [cyan]Financial Metrics:[/cyan]")
        console.print(f"    Price/m²:          €{analysis.price_per_sqm:,.0f}  (district avg: €{analysis.district_avg_price_sqm:,.0f})")
        console.print(f"    vs Market:         {analysis.price_vs_market_pct:+.1f}%")
        console.print(f"    Total Cost:        €{analysis.total_purchase_cost:,.0f}  (incl. 11% fees)")
        console.print(f"    Equity needed:     €{analysis.equity_required:,.0f}")
        console.print(f"    Mortgage payment:  €{analysis.mortgage_monthly:,.0f}/mo")
        console.print(f"    Est. rent:         €{analysis.estimated_rent_monthly:,.0f}/mo")
        console.print(f"    Gross yield:       {analysis.gross_rental_yield_pct:.2f}%")
        console.print(f"    Net yield:         {analysis.net_rental_yield_pct:.2f}%")
        cf = analysis.monthly_cashflow
        cf_color = "green" if cf > 0 else "red"
        console.print(f"    Monthly cashflow:  [{cf_color}]€{cf:+,.0f}[/{cf_color}]")
        console.print(f"    Cash-on-cash:      {analysis.cash_on_cash_return_pct:.2f}%")

        if analysis.undervalue_reasons:
            console.print(f"\n  [cyan]Why Undervalued:[/cyan]")
            for reason in analysis.undervalue_reasons:
                console.print(f"    [green]✓[/green] {reason}")

    console.print(f"\n[dim]Analysis complete. {len(results)} deals scored and ranked.[/dim]")

if __name__ == "__main__":
    main()
