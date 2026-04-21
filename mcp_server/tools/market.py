"""Market data and investment estimation tools."""

from __future__ import annotations

from mcp.server.fastmcp import Context

from mcp_server.models.outputs import (
    DistrictData,
    InvestmentEstimate,
    MarketOverview,
)


def get_lifespan(ctx: Context) -> dict:
    return ctx.request_context.lifespan_context


def register(mcp):
    @mcp.tool()
    def get_market_data(
        districts: list[str] | None = None,
        ctx: Context = None,
    ) -> MarketOverview:
        """Get Hamburg real estate market data for investment context.

        - No districts: city-wide overview of all 7 districts
        - One district: detailed data for that district
        - Multiple districts: side-by-side comparison

        Returns: avg price/m², avg rent/m², gross yield %, market trend,
        and purchase cost breakdown (taxes, notary, etc.).

        Districts: Altona, Eimsbüttel, Hamburg-Mitte, Hamburg-Nord,
        Wandsbek, Bergedorf, Harburg.
        """
        lc = get_lifespan(ctx)
        market = lc["market_data"]

        if districts:
            target_districts = districts
        else:
            target_districts = market.districts

        district_list = []
        for name in target_districts:
            data = market.get_district_data(name)
            if data:
                district_list.append(DistrictData(
                    name=name,
                    avg_price_sqm=data["avg_price_sqm_buy"],
                    avg_rent_sqm=data["avg_rent_sqm_month"],
                    avg_gross_yield_pct=data["avg_gross_yield_pct"],
                    trend=data.get("trend", "stable"),
                    description=data.get("description", ""),
                ))

        return MarketOverview(
            districts=district_list,
            purchase_costs_pct=market.purchase_costs_breakdown,
            total_purchase_costs_pct=market.total_purchase_costs_pct,
        )

    @mcp.tool()
    def estimate_investment(
        purchase_price: float,
        size_sqm: float,
        district: str,
        equity_pct: float = 20,
        interest_rate_pct: float = 3.5,
        loan_term_years: int = 25,
        monthly_hausgeld: float = 0,
        ctx: Context = None,
    ) -> InvestmentEstimate:
        """Calculate investment returns for a hypothetical property.

        No listing needed - just plug in numbers for "what if" scenarios.
        Example: "What would a 200k, 60m² apartment in Harburg yield?"

        Returns: estimated rent, mortgage, cashflow, yield, equity needed.
        """
        lc = get_lifespan(ctx)
        market = lc["market_data"]

        # Purchase costs
        total_pct = market.total_purchase_costs_pct
        total_cost = purchase_price * (1 + total_pct / 100)
        equity = total_cost * (equity_pct / 100)
        loan = total_cost - equity

        # Mortgage (annuity)
        if loan > 0 and interest_rate_pct > 0:
            r = interest_rate_pct / 100 / 12
            n = loan_term_years * 12
            mortgage = loan * (r * (1 + r) ** n) / ((1 + r) ** n - 1)
        else:
            mortgage = 0

        # Rent estimate
        rent = market.estimate_monthly_rent(district, size_sqm)

        # Annual
        annual_rent = rent * 12
        annual_hausgeld = monthly_hausgeld * 12
        annual_reserves = purchase_price * 0.01
        annual_vacancy = annual_rent * 0.03
        annual_costs = annual_hausgeld + annual_reserves + annual_vacancy

        gross_yield = (annual_rent / purchase_price * 100) if purchase_price else 0
        net_income = annual_rent - annual_costs
        net_yield = (net_income / total_cost * 100) if total_cost else 0

        monthly_cf = (net_income - mortgage * 12) / 12
        annual_cf = monthly_cf * 12
        coc = (annual_cf / equity * 100) if equity else 0

        # Market comparison
        avg_sqm = market.get_avg_price_sqm(district)
        price_sqm = purchase_price / size_sqm if size_sqm else 0
        vs_market = ((price_sqm - avg_sqm) / avg_sqm * 100) if avg_sqm else 0

        return InvestmentEstimate(
            purchase_price=purchase_price,
            total_cost=round(total_cost, 2),
            equity_required=round(equity, 2),
            mortgage_monthly=round(mortgage, 2),
            estimated_rent_monthly=round(rent, 2),
            monthly_cashflow=round(monthly_cf, 2),
            gross_yield_pct=round(gross_yield, 2),
            net_yield_pct=round(net_yield, 2),
            cash_on_cash_return_pct=round(coc, 2),
            district=district,
            district_avg_price_sqm=round(avg_sqm, 2),
            price_vs_market_pct=round(vs_market, 1),
        )
