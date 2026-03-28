"""Telegram bot for real estate deal alerts and interactive search."""

from __future__ import annotations

import csv
import json
import logging
import os
from io import StringIO, BytesIO
from concurrent.futures import ThreadPoolExecutor

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from src.database.models import Listing, UserCriteria, AnalysisResult
from src.database.db import Database
from src.scraper.immoscout import ImmoScoutScraper
from src.scraper.kleinanzeigen import KleinanzeigenScraper
from src.scraper.immowelt import ImmoweltScraper
from src.analyzer.market_data import HamburgMarketData
from src.analyzer.scorer import DealScorer

logger = logging.getLogger(__name__)

# Conversation states
BUDGET_MIN, BUDGET_MAX, SIZE, ROOMS, DISTRICTS, EQUITY, INTEREST, TERM, YIELD, RISK = range(10)

HAMBURG_DISTRICTS = [
    "Altona", "Eimsbüttel", "Hamburg-Mitte", "Hamburg-Nord",
    "Wandsbek", "Bergedorf", "Harburg",
]


class TelegramBot:
    """Telegram bot for Hamburg real estate deal hunting."""

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self.token:
            raise ValueError(
                "Telegram bot token required. Set TELEGRAM_BOT_TOKEN env var."
            )
        self.db = Database()
        self.market_data = HamburgMarketData()
        self.scorer = DealScorer(self.market_data)

    def run(self):
        """Start the Telegram bot."""
        app = Application.builder().token(self.token).build()

        # Criteria conversation handler
        criteria_handler = ConversationHandler(
            entry_points=[CommandHandler("criteria", self._criteria_start)],
            states={
                BUDGET_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._budget_min)],
                BUDGET_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._budget_max)],
                SIZE: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._size)],
                ROOMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._rooms)],
                DISTRICTS: [CallbackQueryHandler(self._districts)],
                EQUITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._equity)],
                INTEREST: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._interest)],
                TERM: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._term)],
                YIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._yield_target)],
                RISK: [CallbackQueryHandler(self._risk)],
            },
            fallbacks=[CommandHandler("cancel", self._cancel)],
        )

        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(criteria_handler)
        app.add_handler(CommandHandler("search", self._search))
        app.add_handler(CommandHandler("alerts", self._alerts))
        app.add_handler(CommandHandler("deal", self._deal_detail))
        app.add_handler(CommandHandler("export", self._export))
        app.add_handler(CommandHandler("help", self._help))

        # Schedule daily alerts
        if app.job_queue:
            app.job_queue.run_repeating(
                self._scheduled_alert, interval=86400, first=60
            )

        logger.info("Starting Telegram bot...")
        app.run_polling()

    async def _start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Welcome to the Hamburg Real Estate Deal Finder!\n\n"
            "I scrape ImmoScout24, Kleinanzeigen & Immowelt to find "
            "undervalued properties with good ROI potential.\n\n"
            "Commands:\n"
            "/criteria - Set your search criteria\n"
            "/search - Find deals now\n"
            "/alerts on|off - Daily deal alerts\n"
            "/deal <number> - View deal details\n"
            "/export - Export results as CSV\n"
            "/help - Show help"
        )

    async def _help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Hamburg Deal Finder - Commands\n\n"
            "/criteria - Set budget, size, rooms, districts, financing\n"
            "/search - Scrape platforms & analyze deals\n"
            "/deal 1 - Show detailed analysis for deal #1\n"
            "/alerts on - Enable daily deal alerts\n"
            "/alerts off - Disable alerts\n"
            "/export - Get results as CSV file\n\n"
            "Tip: Set criteria first, then run /search"
        )

    # --- Criteria conversation ---

    async def _criteria_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["criteria"] = {}
        await update.message.reply_text(
            "Let's set your search criteria.\n\n"
            "What is your MINIMUM budget (EUR)?\n"
            "Example: 50000"
        )
        return BUDGET_MIN

    async def _budget_min(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(update.message.text.replace(".", "").replace(",", "."))
            context.user_data["criteria"]["budget_min"] = val
            await update.message.reply_text(
                f"Min budget: {val:,.0f} EUR\n\n"
                "What is your MAXIMUM budget (EUR)?"
            )
            return BUDGET_MAX
        except ValueError:
            await update.message.reply_text("Please enter a number. Example: 50000")
            return BUDGET_MIN

    async def _budget_max(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(update.message.text.replace(".", "").replace(",", "."))
            context.user_data["criteria"]["budget_max"] = val
            await update.message.reply_text(
                f"Max budget: {val:,.0f} EUR\n\n"
                "What is the MINIMUM size in m\u00b2?"
            )
            return SIZE
        except ValueError:
            await update.message.reply_text("Please enter a number. Example: 400000")
            return BUDGET_MAX

    async def _size(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(update.message.text.replace(",", "."))
            context.user_data["criteria"]["min_size_sqm"] = val
            await update.message.reply_text(
                f"Min size: {val:.0f} m\u00b2\n\n"
                "What is the MINIMUM number of rooms?"
            )
            return ROOMS
        except ValueError:
            await update.message.reply_text("Please enter a number. Example: 50")
            return SIZE

    async def _rooms(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(update.message.text.replace(",", "."))
            context.user_data["criteria"]["min_rooms"] = val

            # District selection keyboard
            keyboard = []
            for d in HAMBURG_DISTRICTS:
                avg = self.market_data.get_avg_price_sqm(d)
                keyboard.append([
                    InlineKeyboardButton(
                        f"{d} ({avg:,.0f} EUR/m\u00b2)",
                        callback_data=f"dist_{d}",
                    )
                ])
            keyboard.append([InlineKeyboardButton("All Districts", callback_data="dist_all")])
            keyboard.append([InlineKeyboardButton("Done selecting", callback_data="dist_done")])

            context.user_data["criteria"]["districts"] = []
            await update.message.reply_text(
                "Select preferred districts (tap multiple, then 'Done'):",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return DISTRICTS
        except ValueError:
            await update.message.reply_text("Please enter a number. Example: 2")
            return ROOMS

    async def _districts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        data = query.data
        if data == "dist_all":
            context.user_data["criteria"]["districts"] = []
            await query.edit_message_text("Selected: All Hamburg districts")
        elif data == "dist_done":
            selected = context.user_data["criteria"].get("districts", [])
            label = ", ".join(selected) if selected else "All districts"
            await query.edit_message_text(f"Selected: {label}")
        else:
            district = data.replace("dist_", "")
            dists = context.user_data["criteria"].get("districts", [])
            if district in dists:
                dists.remove(district)
            else:
                dists.append(district)
            context.user_data["criteria"]["districts"] = dists
            await query.edit_message_text(
                f"Selected: {', '.join(dists)}\n\nTap more or press 'Done'."
            )
            return DISTRICTS

        await query.message.reply_text(
            "What percentage of equity will you invest? (e.g., 20)"
        )
        return EQUITY

    async def _equity(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(update.message.text.replace(",", ".").replace("%", ""))
            context.user_data["criteria"]["equity_pct"] = val
            await update.message.reply_text(
                f"Equity: {val:.0f}%\n\n"
                "What interest rate (%)? Example: 3.5"
            )
            return INTEREST
        except ValueError:
            await update.message.reply_text("Please enter a number. Example: 20")
            return EQUITY

    async def _interest(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(update.message.text.replace(",", ".").replace("%", ""))
            context.user_data["criteria"]["interest_rate_pct"] = val
            await update.message.reply_text(
                f"Interest rate: {val:.1f}%\n\n"
                "Loan term in years? Example: 25"
            )
            return TERM
        except ValueError:
            await update.message.reply_text("Please enter a number. Example: 3.5")
            return INTEREST

    async def _term(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = int(update.message.text)
            context.user_data["criteria"]["loan_term_years"] = val
            await update.message.reply_text(
                f"Loan term: {val} years\n\n"
                "Minimum gross rental yield (%)? Example: 4"
            )
            return YIELD
        except ValueError:
            await update.message.reply_text("Please enter a number. Example: 25")
            return TERM

    async def _yield_target(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            val = float(update.message.text.replace(",", ".").replace("%", ""))
            context.user_data["criteria"]["min_gross_yield_pct"] = val

            keyboard = [
                [InlineKeyboardButton("Conservative", callback_data="risk_conservative")],
                [InlineKeyboardButton("Moderate", callback_data="risk_moderate")],
                [InlineKeyboardButton("Aggressive", callback_data="risk_aggressive")],
            ]
            await update.message.reply_text(
                f"Min yield: {val:.1f}%\n\n"
                "Risk tolerance?",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            return RISK
        except ValueError:
            await update.message.reply_text("Please enter a number. Example: 4")
            return YIELD

    async def _risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        risk = query.data.replace("risk_", "")
        context.user_data["criteria"]["risk_tolerance"] = risk

        # Build and save criteria
        c = context.user_data["criteria"]
        criteria = UserCriteria(
            budget_min=c.get("budget_min", 0),
            budget_max=c.get("budget_max", 1_000_000),
            min_size_sqm=c.get("min_size_sqm", 0),
            min_rooms=c.get("min_rooms", 1),
            districts=c.get("districts", []),
            equity_pct=c.get("equity_pct", 20),
            interest_rate_pct=c.get("interest_rate_pct", 3.5),
            loan_term_years=c.get("loan_term_years", 25),
            min_gross_yield_pct=c.get("min_gross_yield_pct", 4.0),
            risk_tolerance=risk,
            chat_id=query.message.chat_id,
        )
        self.db.save_user_criteria(criteria)

        await query.edit_message_text(
            f"Criteria saved!\n\n"
            f"Budget: {criteria.budget_min:,.0f} - {criteria.budget_max:,.0f} EUR\n"
            f"Size: {criteria.min_size_sqm:.0f}+ m\u00b2 | Rooms: {criteria.min_rooms:.0f}+\n"
            f"Districts: {', '.join(criteria.districts) or 'All'}\n"
            f"Financing: {criteria.equity_pct:.0f}% equity, {criteria.interest_rate_pct:.1f}% rate\n"
            f"Risk: {risk}\n\n"
            f"Run /search to find deals!"
        )
        return ConversationHandler.END

    async def _cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Criteria setup cancelled.")
        return ConversationHandler.END

    # --- Search ---

    async def _search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        criteria = self.db.get_user_criteria(chat_id)
        if not criteria:
            await update.message.reply_text(
                "No criteria set. Run /criteria first."
            )
            return

        await update.message.reply_text("Searching ImmoScout24, Kleinanzeigen & Immowelt...")

        # Run scrapers
        all_listings = await self._run_scrapers(criteria)

        if not all_listings:
            await update.message.reply_text(
                "No listings found. Try adjusting your criteria with /criteria."
            )
            return

        # Analyze
        results = self.scorer.score_and_rank(all_listings, criteria)
        for _, analysis in results:
            self.db.save_analysis(analysis)

        # Store for detail lookup
        context.user_data["last_results"] = results[:10]

        # Format results
        msg = self._format_results(results[:10])
        await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

    async def _run_scrapers(self, criteria: UserCriteria) -> list[Listing]:
        scrapers = [
            ImmoScoutScraper(),
            KleinanzeigenScraper(),
            ImmoweltScraper(),
        ]
        all_listings = []

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(s.search, criteria, 3): s for s in scrapers
            }
            for future in futures:
                try:
                    listings = future.result(timeout=120)
                    all_listings.extend(listings)
                except Exception as e:
                    logger.warning(f"Scraper error: {e}")
                finally:
                    futures[future].close()

        self.db.save_listings(all_listings)
        return all_listings

    def _format_results(self, results: list[tuple[Listing, AnalysisResult]]) -> str:
        lines = ["*Top Deals in Hamburg*\n"]
        for i, (listing, analysis) in enumerate(results, 1):
            cf_sign = "+" if analysis.monthly_cashflow >= 0 else ""
            lines.append(
                f"*{i}.* Score: *{analysis.deal_score:.0f}*/100\n"
                f"   {listing.price:,.0f} EUR | {listing.size_sqm:.0f}m\u00b2 | "
                f"{listing.rooms:.0f} rooms\n"
                f"   {listing.district} | {analysis.price_vs_market_pct:+.0f}% vs market\n"
                f"   Yield: {analysis.gross_rental_yield_pct:.1f}% | "
                f"CF: {cf_sign}{analysis.monthly_cashflow:,.0f} EUR/mo\n"
                f"   [{listing.platform}]({listing.url})\n"
            )
            if analysis.undervalue_reasons:
                lines.append(f"   Signals: {', '.join(analysis.undervalue_reasons[:2])}\n")
            lines.append("")

        lines.append("Use /deal <number> for full analysis")
        return "\n".join(lines)

    async def _deal_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        results = context.user_data.get("last_results", [])

        if not args or not args[0].isdigit():
            await update.message.reply_text("Usage: /deal <number> (e.g., /deal 1)")
            return

        idx = int(args[0]) - 1
        if idx < 0 or idx >= len(results):
            await update.message.reply_text(f"Invalid. Choose 1-{len(results)}.")
            return

        listing, analysis = results[idx]
        msg = self._format_detail(listing, analysis)
        await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)

    def _format_detail(self, listing: Listing, analysis: AnalysisResult) -> str:
        cf_sign = "+" if analysis.monthly_cashflow >= 0 else ""
        lines = [
            f"*{listing.title}*\n",
            f"*Price:* {listing.price:,.0f} EUR",
            f"*Size:* {listing.size_sqm:.0f} m\u00b2 | *Rooms:* {listing.rooms:.1f}",
            f"*District:* {listing.district}",
            f"*Address:* {listing.address or 'N/A'}",
            f"*Platform:* [{listing.platform}]({listing.url})\n",
            "*Financial Analysis:*",
            f"  Price/m\u00b2: {analysis.price_per_sqm:,.0f} EUR (avg: {analysis.district_avg_price_sqm:,.0f})",
            f"  vs Market: {analysis.price_vs_market_pct:+.1f}%",
            f"  Total Cost: {analysis.total_purchase_cost:,.0f} EUR",
            f"  Equity Needed: {analysis.equity_required:,.0f} EUR",
            f"  Mortgage: {analysis.mortgage_monthly:,.0f} EUR/mo",
            f"  Est. Rent: {analysis.estimated_rent_monthly:,.0f} EUR/mo",
            f"  Gross Yield: {analysis.gross_rental_yield_pct:.2f}%",
            f"  Net Yield: {analysis.net_rental_yield_pct:.2f}%",
            f"  Cashflow: {cf_sign}{analysis.monthly_cashflow:,.0f} EUR/mo\n",
            f"*Deal Score: {analysis.deal_score:.0f}/100*\n",
        ]

        if analysis.undervalue_reasons:
            lines.append("*Why Undervalued:*")
            for reason in analysis.undervalue_reasons:
                lines.append(f"  \u2713 {reason}")

        return "\n".join(lines)

    async def _alerts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /alerts on or /alerts off")
            return

        if args[0].lower() == "on":
            context.user_data["alerts_enabled"] = True
            await update.message.reply_text(
                "Daily deal alerts enabled! I'll send you new deals every 24h."
            )
        elif args[0].lower() == "off":
            context.user_data["alerts_enabled"] = False
            await update.message.reply_text("Deal alerts disabled.")
        else:
            await update.message.reply_text("Usage: /alerts on or /alerts off")

    async def _export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        results = context.user_data.get("last_results", [])
        if not results:
            await update.message.reply_text("No results to export. Run /search first.")
            return

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Score", "Price", "Size", "Rooms", "District",
            "EUR/m2", "vs_Market", "Yield", "Cashflow", "Platform", "URL",
        ])
        for listing, analysis in results:
            writer.writerow([
                analysis.deal_score, listing.price, listing.size_sqm,
                listing.rooms, listing.district, analysis.price_per_sqm,
                analysis.price_vs_market_pct, analysis.gross_rental_yield_pct,
                analysis.monthly_cashflow, listing.platform, listing.url,
            ])

        buf = BytesIO(output.getvalue().encode("utf-8"))
        buf.name = "hamburg_deals.csv"
        await update.message.reply_document(buf, filename="hamburg_deals.csv")

    async def _scheduled_alert(self, context: ContextTypes.DEFAULT_TYPE):
        """Run periodic scrape and send alerts to subscribed users."""
        # This is a simplified version - in production you'd iterate over all users
        logger.info("Running scheduled deal alert...")
