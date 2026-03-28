"""Interactive CLI bot for real estate deal analysis."""

from __future__ import annotations

import csv
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, FloatPrompt, IntPrompt, Confirm
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.markdown import Markdown

from src.database.models import Listing, UserCriteria, AnalysisResult
from src.database.db import Database
from src.scraper.immoscout import ImmoScoutScraper
from src.scraper.kleinanzeigen import KleinanzeigenScraper
from src.scraper.immowelt import ImmoweltScraper
from src.analyzer.market_data import HamburgMarketData
from src.analyzer.scorer import DealScorer

console = Console()
logger = logging.getLogger(__name__)

HAMBURG_DISTRICTS = [
    "Altona", "Eimsbüttel", "Hamburg-Mitte", "Hamburg-Nord",
    "Wandsbek", "Bergedorf", "Harburg",
]


class CLIBot:
    """Interactive CLI for finding real estate deals in Hamburg."""

    def __init__(self):
        self.db = Database()
        self.market_data = HamburgMarketData()
        self.scorer = DealScorer(self.market_data)

    def run(self):
        """Main bot loop."""
        self._show_welcome()
        criteria = self._collect_criteria()
        listings = self._run_scrapers(criteria)

        if not listings:
            console.print("\n[red]No listings found matching your criteria.[/red]")
            console.print("Try adjusting your budget or size requirements.")
            return

        results = self._analyze_deals(listings, criteria)
        self._show_results(results)
        self._post_results_menu(results, criteria)

    def _show_welcome(self):
        welcome = Panel(
            "[bold]Hamburg Real Estate Deal Finder[/bold]\n\n"
            "I'll help you find undervalued properties in Hamburg.\n"
            "I'll scrape ImmoScout24, Kleinanzeigen & Immowelt,\n"
            "then analyze each deal for investment potential.\n\n"
            "Let's start by setting your search criteria.",
            title="Welcome",
            border_style="blue",
        )
        console.print(welcome)
        console.print()

    def _collect_criteria(self) -> UserCriteria:
        """Interactively collect user search criteria."""
        console.print("[bold cyan]--- Search Criteria ---[/bold cyan]\n")

        # Budget
        console.print("[bold]Budget Range (EUR)[/bold]")
        budget_min = FloatPrompt.ask("  Minimum price", default=50000.0)
        budget_max = FloatPrompt.ask("  Maximum price", default=500000.0)

        # Property type
        console.print("\n[bold]Property Type[/bold]")
        console.print("  1. Apartment (Wohnung)")
        console.print("  2. House (Haus)")
        console.print("  3. Multi-family (Mehrfamilienhaus)")
        console.print("  4. All types")
        type_choice = Prompt.ask("  Choose", choices=["1", "2", "3", "4"], default="1")
        type_map = {
            "1": ["apartment"],
            "2": ["house"],
            "3": ["multi_family"],
            "4": ["apartment", "house", "multi_family"],
        }
        property_types = type_map[type_choice]

        # Size
        console.print("\n[bold]Size (m\u00b2)[/bold]")
        min_size = FloatPrompt.ask("  Minimum size", default=30.0)

        # Rooms
        min_rooms = FloatPrompt.ask("\n[bold]Minimum rooms[/bold]", default=2.0)

        # Districts
        console.print("\n[bold]Hamburg Districts[/bold]")
        for i, d in enumerate(HAMBURG_DISTRICTS, 1):
            avg_price = self.market_data.get_avg_price_sqm(d)
            avg_rent = self.market_data.get_avg_rent_sqm(d)
            trend = self.market_data.get_district_trend(d)
            trend_icon = {"rising": "+", "stable": "=", "declining": "-"}.get(trend, "?")
            console.print(
                f"  {i}. {d:<16} "
                f"avg {avg_price:,.0f} EUR/m\u00b2  |  "
                f"rent {avg_rent:.1f} EUR/m\u00b2  |  "
                f"trend: {trend_icon}"
            )
        console.print(f"  0. All districts")
        district_input = Prompt.ask(
            "  Choose districts (comma-separated numbers)",
            default="0",
        )
        if district_input.strip() == "0":
            districts = []
        else:
            indices = [int(x.strip()) for x in district_input.split(",") if x.strip().isdigit()]
            districts = [HAMBURG_DISTRICTS[i - 1] for i in indices if 1 <= i <= len(HAMBURG_DISTRICTS)]

        # Financing
        console.print("\n[bold]Financing Parameters[/bold]")
        equity_pct = FloatPrompt.ask("  Equity percentage (%)", default=20.0)
        interest_rate = FloatPrompt.ask("  Interest rate (%)", default=3.5)
        loan_term = IntPrompt.ask("  Loan term (years)", default=25)

        # Targets
        console.print("\n[bold]Investment Targets[/bold]")
        min_yield = FloatPrompt.ask("  Minimum gross rental yield (%)", default=4.0)
        min_cashflow = FloatPrompt.ask("  Minimum monthly cashflow (EUR)", default=0.0)

        # Risk tolerance
        console.print("\n[bold]Risk Tolerance[/bold]")
        console.print("  1. Conservative - Prioritize stable cashflow & safe locations")
        console.print("  2. Moderate - Balanced approach")
        console.print("  3. Aggressive - Hunt for deep discounts & value-add")
        risk_choice = Prompt.ask("  Choose", choices=["1", "2", "3"], default="2")
        risk_map = {"1": "conservative", "2": "moderate", "3": "aggressive"}

        criteria = UserCriteria(
            budget_min=budget_min,
            budget_max=budget_max,
            min_size_sqm=min_size,
            min_rooms=min_rooms,
            property_types=property_types,
            districts=districts,
            equity_pct=equity_pct,
            interest_rate_pct=interest_rate,
            loan_term_years=loan_term,
            min_gross_yield_pct=min_yield,
            min_cashflow_monthly=min_cashflow,
            risk_tolerance=risk_map[risk_choice],
        )

        console.print("\n[green]Criteria set. Starting search...[/green]\n")
        return criteria

    def _run_scrapers(self, criteria: UserCriteria) -> list[Listing]:
        """Run all scrapers in parallel."""
        scrapers = [
            ImmoScoutScraper(),
            KleinanzeigenScraper(),
            ImmoweltScraper(),
        ]

        all_listings: list[Listing] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Scraping platforms...", total=len(scrapers))

            with ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    executor.submit(scraper.search, criteria, 3): scraper
                    for scraper in scrapers
                }

                for future in as_completed(futures):
                    scraper = futures[future]
                    try:
                        listings = future.result()
                        all_listings.extend(listings)
                        progress.console.print(
                            f"  [green]{scraper.PLATFORM_NAME}[/green]: "
                            f"found {len(listings)} listings"
                        )
                    except Exception as e:
                        progress.console.print(
                            f"  [red]{scraper.PLATFORM_NAME}[/red]: error - {e}"
                        )
                    finally:
                        scraper.close()
                    progress.advance(task)

        # Save to DB
        new, updated = self.db.save_listings(all_listings)
        console.print(f"\n[dim]Saved {new} new, {updated} updated listings to database[/dim]")

        return all_listings

    def _analyze_deals(
        self, listings: list[Listing], criteria: UserCriteria
    ) -> list[tuple[Listing, AnalysisResult]]:
        """Score and rank all listings."""
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Analyzing deals...", total=1)
            results = self.scorer.score_and_rank(listings, criteria)

            # Save analysis results
            for _, analysis in results:
                self.db.save_analysis(analysis)

            progress.advance(task)

        # Filter by user's minimum criteria
        filtered = [
            (l, a) for l, a in results
            if a.gross_rental_yield_pct >= criteria.min_gross_yield_pct
            and a.monthly_cashflow >= criteria.min_cashflow_monthly
        ]

        # If filtering removes everything, show all results
        if not filtered and results:
            console.print(
                f"\n[yellow]No deals meet your yield/cashflow targets. "
                f"Showing all {len(results)} results.[/yellow]"
            )
            return results[:20]

        return filtered[:20]

    def _show_results(self, results: list[tuple[Listing, AnalysisResult]]):
        """Display results as a rich table."""
        if not results:
            return

        console.print()
        console.print(
            Panel(
                f"[bold]Top {len(results)} Deals Found[/bold]",
                border_style="green",
            )
        )

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("#", style="dim", width=3)
        table.add_column("Score", justify="right", width=6)
        table.add_column("Price", justify="right", width=12)
        table.add_column("Size", justify="right", width=8)
        table.add_column("Rooms", justify="right", width=5)
        table.add_column("District", width=14)
        table.add_column("EUR/m\u00b2", justify="right", width=9)
        table.add_column("vs Mkt", justify="right", width=7)
        table.add_column("Yield", justify="right", width=6)
        table.add_column("CF/mo", justify="right", width=8)
        table.add_column("Signals", width=3, justify="right")
        table.add_column("Platform", width=12)

        for i, (listing, analysis) in enumerate(results, 1):
            # Color code the score
            score = analysis.deal_score
            if score >= 70:
                score_str = f"[bold green]{score:.0f}[/bold green]"
            elif score >= 50:
                score_str = f"[yellow]{score:.0f}[/yellow]"
            else:
                score_str = f"[dim]{score:.0f}[/dim]"

            # Color code market comparison
            vs_mkt = analysis.price_vs_market_pct
            if vs_mkt < -15:
                mkt_str = f"[bold green]{vs_mkt:+.0f}%[/bold green]"
            elif vs_mkt < 0:
                mkt_str = f"[green]{vs_mkt:+.0f}%[/green]"
            else:
                mkt_str = f"[red]{vs_mkt:+.0f}%[/red]"

            # Color code cashflow
            cf = analysis.monthly_cashflow
            if cf > 0:
                cf_str = f"[green]{cf:+,.0f}[/green]"
            else:
                cf_str = f"[red]{cf:+,.0f}[/red]"

            table.add_row(
                str(i),
                score_str,
                f"{listing.price:,.0f}",
                f"{listing.size_sqm:.0f} m\u00b2",
                f"{listing.rooms:.1f}",
                listing.district or "?",
                f"{analysis.price_per_sqm:,.0f}",
                mkt_str,
                f"{analysis.gross_rental_yield_pct:.1f}%",
                cf_str,
                str(len(analysis.undervalue_reasons)),
                listing.platform,
            )

        console.print(table)
        console.print(
            "\n[dim]Score: 0-100 (higher = better deal). "
            "CF/mo = monthly cashflow after mortgage. "
            "Signals = undervalue indicators.[/dim]"
        )

    def _show_detail(self, listing: Listing, analysis: AnalysisResult):
        """Show detailed analysis for a single listing."""
        console.print()
        console.print(Panel(f"[bold]{listing.title}[/bold]", border_style="blue"))

        # Basic info
        info_table = Table(show_header=False, box=None)
        info_table.add_column("Key", style="bold", width=22)
        info_table.add_column("Value")

        info_table.add_row("Platform", listing.platform)
        info_table.add_row("URL", listing.url)
        info_table.add_row("Price", f"\u20ac{listing.price:,.0f}")
        info_table.add_row("Size", f"{listing.size_sqm:.0f} m\u00b2")
        info_table.add_row("Rooms", f"{listing.rooms:.1f}")
        info_table.add_row("District", listing.district or "Unknown")
        info_table.add_row("Address", listing.address or "N/A")
        if listing.year_built:
            info_table.add_row("Year Built", str(listing.year_built))
        if listing.condition:
            info_table.add_row("Condition", listing.condition)
        if listing.hausgeld:
            info_table.add_row("Hausgeld", f"\u20ac{listing.hausgeld:,.0f}/month")

        features = []
        if listing.balcony: features.append("Balcony")
        if listing.garden: features.append("Garden")
        if listing.parking: features.append("Parking")
        if features:
            info_table.add_row("Features", ", ".join(features))

        console.print(info_table)

        # Financial analysis
        console.print("\n[bold cyan]Financial Analysis[/bold cyan]")
        fin_table = Table(show_header=False, box=None)
        fin_table.add_column("Metric", style="bold", width=26)
        fin_table.add_column("Value", justify="right")

        fin_table.add_row("Price per m\u00b2", f"\u20ac{analysis.price_per_sqm:,.0f}")
        fin_table.add_row("District avg m\u00b2", f"\u20ac{analysis.district_avg_price_sqm:,.0f}")
        fin_table.add_row("Price vs Market", f"{analysis.price_vs_market_pct:+.1f}%")
        fin_table.add_row("", "")
        fin_table.add_row("Total Purchase Cost", f"\u20ac{analysis.total_purchase_cost:,.0f}")
        fin_table.add_row("Equity Required", f"\u20ac{analysis.equity_required:,.0f}")
        fin_table.add_row("Monthly Mortgage", f"\u20ac{analysis.mortgage_monthly:,.0f}")
        fin_table.add_row("", "")
        fin_table.add_row("Est. Monthly Rent", f"\u20ac{analysis.estimated_rent_monthly:,.0f}")
        fin_table.add_row("Gross Rental Yield", f"{analysis.gross_rental_yield_pct:.2f}%")
        fin_table.add_row("Net Rental Yield", f"{analysis.net_rental_yield_pct:.2f}%")
        fin_table.add_row("Cap Rate", f"{analysis.cap_rate_pct:.2f}%")
        fin_table.add_row("Cash-on-Cash Return", f"{analysis.cash_on_cash_return_pct:.2f}%")
        fin_table.add_row("", "")

        cf = analysis.monthly_cashflow
        cf_style = "green" if cf > 0 else "red"
        fin_table.add_row("Monthly Cashflow", f"[{cf_style}]\u20ac{cf:+,.0f}[/{cf_style}]")

        console.print(fin_table)

        # Deal score
        score = analysis.deal_score
        score_color = "green" if score >= 70 else "yellow" if score >= 50 else "red"
        console.print(f"\n[bold]Deal Score: [{score_color}]{score:.0f}/100[/{score_color}][/bold]")

        # Undervalue reasons
        if analysis.undervalue_reasons:
            console.print("\n[bold cyan]Why This Deal May Be Undervalued[/bold cyan]")
            for reason in analysis.undervalue_reasons:
                console.print(f"  [green]\u2713[/green] {reason}")
        else:
            console.print("\n[dim]No specific undervalue signals detected.[/dim]")

    def _post_results_menu(
        self,
        results: list[tuple[Listing, AnalysisResult]],
        criteria: UserCriteria,
    ):
        """Post-results interactive menu."""
        while True:
            console.print("\n[bold]Options:[/bold]")
            console.print("  [cyan]1-N[/cyan]  View deal details (enter number)")
            console.print("  [cyan]e[/cyan]    Export results to CSV")
            console.print("  [cyan]j[/cyan]    Export results to JSON")
            console.print("  [cyan]r[/cyan]    Re-run with new criteria")
            console.print("  [cyan]q[/cyan]    Quit")

            choice = Prompt.ask("\nChoice", default="q")

            if choice.lower() == "q":
                console.print("[dim]Goodbye![/dim]")
                break
            elif choice.lower() == "e":
                self._export_csv(results)
            elif choice.lower() == "j":
                self._export_json(results)
            elif choice.lower() == "r":
                criteria = self._collect_criteria()
                listings = [l for l, _ in results]
                results = self._analyze_deals(listings, criteria)
                self._show_results(results)
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(results):
                    listing, analysis = results[idx]
                    self._show_detail(listing, analysis)
                else:
                    console.print("[red]Invalid number.[/red]")

    def _export_csv(self, results: list[tuple[Listing, AnalysisResult]]):
        path = "data/deals_export.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Score", "Price", "Size_sqm", "Rooms", "District", "EUR_per_sqm",
                "vs_Market_%", "Gross_Yield_%", "Monthly_CF", "Platform", "URL",
                "Undervalue_Reasons",
            ])
            for listing, analysis in results:
                writer.writerow([
                    analysis.deal_score,
                    listing.price,
                    listing.size_sqm,
                    listing.rooms,
                    listing.district,
                    analysis.price_per_sqm,
                    analysis.price_vs_market_pct,
                    analysis.gross_rental_yield_pct,
                    analysis.monthly_cashflow,
                    listing.platform,
                    listing.url,
                    "; ".join(analysis.undervalue_reasons),
                ])
        console.print(f"[green]Exported to {path}[/green]")

    def _export_json(self, results: list[tuple[Listing, AnalysisResult]]):
        path = "data/deals_export.json"
        data = []
        for listing, analysis in results:
            data.append({
                "listing": listing.to_dict(),
                "analysis": analysis.to_dict(),
            })
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        console.print(f"[green]Exported to {path}[/green]")
