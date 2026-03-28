"""Investment metrics calculator for real estate deals."""

from __future__ import annotations

from dataclasses import dataclass

from src.database.models import Listing, UserCriteria, AnalysisResult
from .market_data import HamburgMarketData


class MetricsCalculator:
    """Calculate investment metrics for a listing based on user criteria."""

    def __init__(self, market_data: HamburgMarketData):
        self.market = market_data

    def calculate(self, listing: Listing, criteria: UserCriteria) -> AnalysisResult:
        """Calculate all investment metrics for a listing."""
        district = listing.district or "Hamburg"

        # Basic price metrics
        price_per_sqm = listing.price_per_sqm
        district_avg = self.market.get_avg_price_sqm(district)
        price_vs_market = ((price_per_sqm - district_avg) / district_avg * 100) if district_avg else 0

        # Purchase costs
        purchase_costs_pct = self.market.total_purchase_costs_pct
        total_purchase_cost = listing.price * (1 + purchase_costs_pct / 100)

        # Financing
        equity_pct = criteria.equity_pct / 100
        equity_required = total_purchase_cost * equity_pct
        loan_amount = total_purchase_cost - equity_required

        mortgage_monthly = self._calculate_mortgage(
            loan_amount, criteria.interest_rate_pct, criteria.loan_term_years
        )

        # Rental income
        estimated_rent = self.market.estimate_monthly_rent(district, listing.size_sqm)

        # Annual figures
        annual_rent = estimated_rent * 12
        annual_hausgeld = (listing.hausgeld or 0) * 12
        annual_reserves = listing.price * 0.01  # 1% maintenance reserve
        annual_vacancy = annual_rent * 0.03  # 3% vacancy assumption
        annual_costs = annual_hausgeld + annual_reserves + annual_vacancy

        # Yields
        gross_yield = (annual_rent / listing.price * 100) if listing.price else 0
        net_income = annual_rent - annual_costs
        net_yield = (net_income / total_purchase_cost * 100) if total_purchase_cost else 0

        # NOI and cap rate
        noi = net_income
        cap_rate = (noi / total_purchase_cost * 100) if total_purchase_cost else 0

        # Cashflow
        annual_mortgage = mortgage_monthly * 12
        monthly_cashflow = (net_income - annual_mortgage) / 12

        # Cash-on-cash return
        annual_cashflow = monthly_cashflow * 12
        coc_return = (annual_cashflow / equity_required * 100) if equity_required else 0

        return AnalysisResult(
            listing_id=listing.id,
            price_per_sqm=round(price_per_sqm, 2),
            district_avg_price_sqm=round(district_avg, 2),
            price_vs_market_pct=round(price_vs_market, 1),
            estimated_rent_monthly=round(estimated_rent, 2),
            gross_rental_yield_pct=round(gross_yield, 2),
            net_rental_yield_pct=round(net_yield, 2),
            cap_rate_pct=round(cap_rate, 2),
            monthly_cashflow=round(monthly_cashflow, 2),
            cash_on_cash_return_pct=round(coc_return, 2),
            total_purchase_cost=round(total_purchase_cost, 2),
            mortgage_monthly=round(mortgage_monthly, 2),
            equity_required=round(equity_required, 2),
        )

    @staticmethod
    def _calculate_mortgage(
        loan_amount: float, annual_rate_pct: float, term_years: int
    ) -> float:
        """Calculate monthly mortgage payment (annuity)."""
        if loan_amount <= 0 or annual_rate_pct <= 0:
            return 0.0

        monthly_rate = annual_rate_pct / 100 / 12
        n_payments = term_years * 12

        # Annuity formula
        payment = loan_amount * (
            monthly_rate * (1 + monthly_rate) ** n_payments
        ) / ((1 + monthly_rate) ** n_payments - 1)

        return payment
