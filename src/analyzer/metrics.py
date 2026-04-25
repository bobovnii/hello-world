"""Investment metrics calculator for real estate deals."""

from __future__ import annotations

from src.database.models import Listing, UserCriteria, AnalysisResult
from .market_data import HamburgMarketData


class MetricsCalculator:
    """Calculate investment metrics for a listing based on user criteria."""

    def __init__(self, market_data: HamburgMarketData):
        self.market = market_data

    def calculate(self, listing: Listing, criteria: UserCriteria) -> AnalysisResult:
        """Calculate all investment metrics for a listing.

        Rent precedence: when ``listing.is_rented`` AND
        ``listing.current_rent_monthly`` is a positive number, use the
        actual contractual rent for yield/cashflow/cap-rate. Wishful
        market estimates inflate yields on tenanted units that are
        often capped by old contracts or Sozialbindung. Only fall back
        to the market estimate when the unit is vacant or the actual
        rent is unknown.

        Purchase costs are platform-aware: see ``_purchase_costs_pct``
        for the commission-free carve-out.
        """
        district = listing.district or "Hamburg"

        # Basic price metrics
        price_per_sqm = listing.price_per_sqm
        district_avg = self.market.get_avg_price_sqm(district)
        price_vs_market = ((price_per_sqm - district_avg) / district_avg * 100) if district_avg else 0

        # Purchase costs (platform-aware: commission-free → drop Maklercourtage)
        purchase_costs_pct = self._purchase_costs_pct(listing)
        total_purchase_cost = listing.price * (1 + purchase_costs_pct / 100)

        # Financing
        equity_pct = criteria.equity_pct / 100
        equity_required = total_purchase_cost * equity_pct
        loan_amount = total_purchase_cost - equity_required

        mortgage_monthly = self._calculate_mortgage(
            loan_amount, criteria.interest_rate_pct, criteria.loan_term_years
        )

        # Rental income - zero for uninhabitable properties
        is_uninhabitable = (
            listing.is_ausbau_needed
            or (listing.condition and listing.condition.lower() in ("rohbau", "shell"))
        )
        if is_uninhabitable:
            estimated_rent = 0.0
        elif (
            listing.is_rented
            and listing.current_rent_monthly is not None
            and listing.current_rent_monthly > 0
        ):
            # Tenanted with known rent: trust the contract, not market wishful
            # thinking. Old contracts / Sozialbindung often cap below market.
            estimated_rent = float(listing.current_rent_monthly)
        else:
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

    # Platforms that are typically commission-free (no Maklercourtage).
    _COMMISSION_FREE_PLATFORMS: frozenset[str] = frozenset(
        {"ohne-makler", "kleinanzeigen"}
    )

    # Description markers that indicate the seller is paying no commission
    # regardless of platform.
    _COMMISSION_FREE_MARKERS: tuple[str, ...] = (
        "provisionsfrei",
        "ohne maklerprovision",
        "ohne provision",
        "courtagefrei",
        "maklerfrei",
    )

    def _purchase_costs_pct(self, listing: Listing) -> float:
        """Total Nebenkosten as percentage of purchase price.

        Returns the full purchase costs from market data
        (Grunderwerbsteuer + Notar + Grundbuch + Maklercourtage) by
        default. When the listing is on a commission-free platform
        (``ohne-makler``, ``kleinanzeigen``) OR the description carries
        an explicit commission-free marker (``provisionsfrei`` etc.),
        the Maklercourtage component is dropped from the total.

        The breakdown is read from ``hamburg_market_data.json`` so
        editing the JSON re-tunes the calculation without code changes.
        """
        breakdown = self.market.purchase_costs_breakdown
        platform = (listing.platform or "").lower().strip()
        desc_lower = (listing.description or "").lower()
        commission_free = (
            platform in self._COMMISSION_FREE_PLATFORMS
            or any(marker in desc_lower for marker in self._COMMISSION_FREE_MARKERS)
        )
        if commission_free:
            return sum(v for k, v in breakdown.items() if k != "makler")
        return sum(breakdown.values())

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
