"""Investor view of a listing: what a buy-to-let investor / Makler checks first.

Pure functions, no I/O. Everything is driven by ``InvestmentAssumptions``
so a search can override any number from YAML (``investment:`` block).

German conventions used (all simplifications are explicit):

- **Kaltmiete** is what the investor keeps. Umlagefähige Betriebskosten
  (heating, water, Grundsteuer, cleaning ...) are passed to the tenant, so
  only the *nicht umlagefähig* share of the Hausgeld (WEG-Verwaltung +
  Instandhaltungsrücklage, ~35 %) is a real cost. When the listing has no
  Hausgeld (ImmoScout/Immowelt cards rarely show it) we estimate it per m².
- **Instandhaltung Sondereigentum** (inside the flat): €/m²/year.
  Houses / MFH carry the whole building: higher €/m² + Verwaltung.
- **Kaufpreisfaktor** = Kaufpreis / Jahreskaltmiete — the first number a
  German investor looks at. Compared with the district's own factor
  (100 / district gross yield).
- **Tax**: rental profit is taxed at the personal marginal rate; losses
  offset other income (refund). AfA (depreciation) on the building share
  of the all-in price: 2.5 % (BJ < 1925), 2 % (1925–2022), 3 % (2023+).
- **10-year hold** = the Spekulationsfrist: a private sale after 10 years
  is income-tax-free, which is why German buy-to-let is modelled on 10y.
- Sitting tenants: rent can rise towards market at most by the
  Kappungsgrenze (15 % / 3 years in tight markets, else 20 %).
- Appreciation is applied to the *purchase price*, not to the district
  average — buying below market is not booked as instant profit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from typing import Any

from src.database.models import Listing, UserCriteria


# ---------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------

@dataclass
class InvestmentAssumptions:
    hold_years: int = 10
    tax_rate_pct: float = 42.0            # personal marginal income tax rate
    building_share_apartment: float = 0.75  # share of all-in price that is building (AfA base)
    building_share_house: float = 0.60
    vacancy_pct: float = 3.0              # Mietausfallwagnis, % of rent
    hausgeld_nonrecoverable_share: float = 0.35
    est_nonrecoverable_sqm_month: float = 1.30   # if Hausgeld unknown (Verwaltung + Rücklage)
    sondereigentum_maint_sqm_year: float = 8.0   # apartment interior repairs
    house_maint_sqm_year: float = 15.0           # whole building (house / MFH)
    house_admin_pct_rent: float = 4.0            # MFH Verwaltung, % of rent
    cost_inflation_pct: float = 2.0
    sale_cost_pct: float = 3.0
    # Erbbaurecht (leasehold): ground rent as % of land value p.a., and a
    # haircut on appreciation (the remaining lease term shrinks every year).
    erbbauzins_pct_land: float = 3.5
    erbbau_appreciation_pp: float = -1.0
    # Market growth by district trend (nominal % p.a.), base scenario.
    rent_growth_pct: dict[str, float] = field(
        default_factory=lambda: {"rising": 2.5, "stable": 2.0, "declining": 1.0}
    )
    appreciation_pct: dict[str, float] = field(
        default_factory=lambda: {"rising": 2.0, "stable": 1.5, "declining": 0.5}
    )
    # Scenario shifts vs base (percentage points).
    bear_appreciation_pp: float = -2.0
    bear_rent_growth_pp: float = -1.0
    bull_appreciation_pp: float = 1.5
    bull_rent_growth_pp: float = 0.5
    # Kappungsgrenze for sitting tenants (% per 3 years) by city slug.
    rent_cap_pct_3y: dict[str, float] = field(
        default_factory=lambda: {"hamburg": 15.0, "berlin": 15.0, "dresden": 15.0}
    )
    default_rent_cap_pct_3y: float = 20.0

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> "InvestmentAssumptions":
        """Build from a YAML mapping; unknown keys raise so typos are loud."""
        base = cls()
        if not cfg:
            return base
        known = {f.name for f in fields(cls)}
        unknown = set(cfg) - known
        if unknown:
            raise ValueError(f"unknown investment assumption(s): {sorted(unknown)}")
        updates = {}
        for k, v in cfg.items():
            cur = getattr(base, k)
            if isinstance(cur, dict):
                merged = dict(cur)
                merged.update(v or {})
                updates[k] = merged
            else:
                updates[k] = type(cur)(v)
        return replace(base, **updates)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------

@dataclass
class YearRow:
    year: int
    rent: float              # annual Kaltmiete collected (after vacancy)
    costs: float             # non-recoverable operating costs
    interest: float
    principal: float
    cf_pre_tax: float
    tax: float               # positive = tax paid, negative = refund
    cf_after_tax: float
    loan_balance: float      # end of year
    property_value: float    # end of year


@dataclass
class ScenarioResult:
    name: str
    appreciation_pct: float
    rent_growth_pct: float
    irr_pre_tax_pct: float | None
    irr_after_tax_pct: float | None
    equity_multiple: float | None    # after tax
    profit_after_tax: float          # total € gained over the hold, after tax
    exit_equity: float               # sale price - sale costs - remaining loan
    rows: list[YearRow] = field(default_factory=list)


@dataclass
class InvestmentView:
    # Rent
    rent_month: float                 # Kaltmiete used for the analysis
    rent_sqm: float
    rent_source: str                  # "Ist-Miete" / "Markt-Ø <Bezirk>"
    market_rent_month: float
    rent_potential_pct: float | None  # market vs current rent (tenanted only)
    # Price multiples
    kaufpreisfaktor: float | None
    district_factor: float | None
    fair_value: float | None          # market rent × district factor
    fair_value_gap_pct: float | None  # price vs fair value (neg = cheaper)
    # Purchase side
    grunderwerbsteuer: float
    notar_grundbuch: float
    makler: float
    equity: float
    loan: float
    ltv_pct: float
    # Year-1 operations
    opex_year1: float
    opex_estimated: bool
    noi_year1: float
    dscr: float | None
    interest_month_y1: float
    principal_month_y1: float
    cf_pre_tax_month: float
    tax_month: float
    cf_after_tax_month: float
    afa_year: float
    afa_rate_pct: float
    break_even_rent_sqm: float | None
    # 10-year
    hold_years: int
    base: ScenarioResult | None
    bear: ScenarioResult | None
    bull: ScenarioResult | None
    assumptions_note: str = ""
    erbbauzins_year: float = 0.0          # estimated ground rent (leasehold only)
    tenanted_rent_unknown: bool = False   # rented, but contract rent not in listing


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

def is_house_like(listing: Listing) -> bool:
    return (listing.property_type or "apartment") in ("house", "multi_family")


def erbbauzins_annual(listing: Listing, a: InvestmentAssumptions, district_price_sqm: float) -> float:
    """Estimated yearly ground rent for an Erbbaurecht (leasehold) unit.

    Listings rarely state it. Erbbauzins is typically 3–4 % of the land
    value per year; the land value is approximated as the land share of
    the *market* value (district €/m² × size), not of the discounted price.
    """
    if not listing.is_erbbaurecht or district_price_sqm <= 0:
        return 0.0
    land_share = 1 - (a.building_share_house if is_house_like(listing) else a.building_share_apartment)
    land_value = district_price_sqm * (listing.size_sqm or 0.0) * land_share
    return land_value * a.erbbauzins_pct_land / 100


def annual_operating_costs(
    listing: Listing,
    annual_rent: float,
    a: InvestmentAssumptions,
    district_price_sqm: float = 0.0,
) -> tuple[float, bool]:
    """Year-1 non-recoverable costs the landlord pays out of Kaltmiete.

    Returns (costs, estimated) — ``estimated`` is True when the Hausgeld
    (or the Erbbauzins) was not in the listing and had to be assumed.
    """
    size = max(listing.size_sqm or 0.0, 0.0)
    vacancy = annual_rent * a.vacancy_pct / 100
    ground_rent = erbbauzins_annual(listing, a, district_price_sqm)
    if is_house_like(listing):
        maint = size * a.house_maint_sqm_year
        admin = annual_rent * a.house_admin_pct_rent / 100
        return maint + admin + vacancy + ground_rent, ground_rent > 0
    if listing.hausgeld and listing.hausgeld > 0:
        weg = listing.hausgeld * 12 * a.hausgeld_nonrecoverable_share
        estimated = False
    else:
        weg = size * a.est_nonrecoverable_sqm_month * 12
        estimated = True
    maint = size * a.sondereigentum_maint_sqm_year
    return weg + maint + vacancy + ground_rent, estimated or ground_rent > 0


def afa_rate_pct(year_built: int | None) -> float:
    if year_built and year_built >= 2023:
        return 3.0
    if year_built and year_built < 1925:
        return 2.5
    return 2.0


def annuity_monthly(loan: float, rate_pct: float, years: int) -> float:
    if loan <= 0:
        return 0.0
    if rate_pct <= 0:
        return loan / (years * 12)
    r = rate_pct / 100 / 12
    n = years * 12
    return loan * r * (1 + r) ** n / ((1 + r) ** n - 1)


def irr(cashflows: list[float]) -> float | None:
    """Annual IRR via bisection. None when no sign change / no root."""
    if not cashflows or all(c >= 0 for c in cashflows) or all(c <= 0 for c in cashflows):
        return None

    def npv(rate: float) -> float:
        return sum(c / (1 + rate) ** t for t, c in enumerate(cashflows))

    lo, hi = -0.99, 1.0
    f_lo, f_hi = npv(lo), npv(hi)
    while f_lo * f_hi > 0 and hi < 100:
        hi *= 2
        f_hi = npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def _project(
    *,
    name: str,
    a: InvestmentAssumptions,
    price: float,
    equity: float,
    loan: float,
    interest_pct: float,
    monthly_payment: float,
    rent0_month: float,
    market_rent0_month: float,
    tenanted_below_market: bool,
    rent_cap_3y: float,
    rent_growth: float,
    appreciation: float,
    afa_year: float,
    opex0: float,
) -> ScenarioResult:
    years = a.hold_years
    r_m = interest_pct / 100 / 12
    balance = loan
    rent_m = rent0_month
    market_m = market_rent0_month
    rows: list[YearRow] = []
    cfs_pre = [-equity]
    cfs_after = [-equity]
    total_afa = 0.0

    for y in range(1, years + 1):
        if y > 1:
            market_m *= 1 + rent_growth / 100
            if tenanted_below_market:
                # Sitting tenant: step-ups every 3 years, capped, never above market.
                if (y - 1) % 3 == 0:
                    rent_m = min(market_m, rent_m * (1 + rent_cap_3y / 100))
                if rent_m >= market_m:
                    tenanted_below_market = False
            else:
                rent_m = market_m
        rent_y = rent_m * 12
        rent_net_vacancy = rent_y * (1 - a.vacancy_pct / 100)
        costs_y = (opex0 - rent0_month * 12 * a.vacancy_pct / 100) * (
            (1 + a.cost_inflation_pct / 100) ** (y - 1)
        )

        interest_y = principal_y = 0.0
        for _ in range(12):
            if balance <= 0:
                break
            i = balance * r_m
            p = min(monthly_payment - i, balance)
            interest_y += i
            principal_y += p
            balance -= p

        cf_pre = rent_net_vacancy - costs_y - interest_y - principal_y
        taxable = rent_net_vacancy - costs_y - interest_y - afa_year
        tax = taxable * a.tax_rate_pct / 100
        total_afa += afa_year
        cf_after = cf_pre - tax
        value = price * (1 + appreciation / 100) ** y
        rows.append(YearRow(y, rent_net_vacancy, costs_y, interest_y, principal_y,
                            cf_pre, tax, cf_after, max(balance, 0.0), value))
        cfs_pre.append(cf_pre)
        cfs_after.append(cf_after)

    sale_value = rows[-1].property_value if rows else price
    exit_equity = sale_value * (1 - a.sale_cost_pct / 100) - max(balance, 0.0)
    # Private sale inside the Spekulationsfrist is taxed (gain incl. AfA recapture).
    exit_tax = 0.0
    if years < 10:
        basis = price - total_afa
        gain = sale_value * (1 - a.sale_cost_pct / 100) - basis
        exit_tax = max(gain, 0.0) * a.tax_rate_pct / 100
    cfs_pre[-1] += exit_equity
    cfs_after[-1] += exit_equity - exit_tax

    irr_pre = irr(cfs_pre)
    irr_after = irr(cfs_after)
    profit_after = sum(cfs_after)  # includes -equity at t0
    multiple = (sum(cfs_after[1:]) / equity) if equity > 0 else None
    return ScenarioResult(
        name=name,
        appreciation_pct=appreciation,
        rent_growth_pct=rent_growth,
        irr_pre_tax_pct=None if irr_pre is None else irr_pre * 100,
        irr_after_tax_pct=None if irr_after is None else irr_after * 100,
        equity_multiple=multiple,
        profit_after_tax=profit_after,
        exit_equity=exit_equity - exit_tax,
        rows=rows,
    )


def build_investment_view(
    listing: Listing,
    criteria: UserCriteria,
    market,  # CityMarketData
    *,
    rent_month: float,
    market_rent_month: float,
    total_purchase_cost: float,
    purchase_costs_breakdown: dict[str, float],
    commission_free: bool,
    equity: float,
    loan: float,
    monthly_payment: float,
    assumptions: InvestmentAssumptions | None = None,
) -> InvestmentView:
    a = assumptions or InvestmentAssumptions()
    district = listing.district or market.display_name
    price = listing.price
    size = listing.size_sqm or 0.0

    tenanted = bool(listing.is_rented and listing.current_rent_monthly and listing.current_rent_monthly > 0)
    if tenanted:
        rent_source = "Ist-Miete (Vertrag)"
    else:
        known = market.get_district_data(district) is not None
        rent_source = f"Markt-Ø {district}" if known else f"Markt-Ø {market.display_name} (Stadt)"
    rent_potential = None
    if tenanted and rent_month > 0 and market_rent_month > 0:
        rent_potential = (market_rent_month / rent_month - 1) * 100

    annual_rent = rent_month * 12
    annual_market_rent = market_rent_month * 12

    # Multiples
    kpf = price / annual_rent if annual_rent > 0 else None
    d_yield = market.get_avg_gross_yield(district)
    d_factor = 100 / d_yield if d_yield else None
    fair = annual_market_rent * d_factor if (d_factor and annual_market_rent > 0) else None
    fair_gap = (price / fair - 1) * 100 if fair else None

    # Purchase costs in €
    gest = price * purchase_costs_breakdown.get("grunderwerbsteuer", 0) / 100
    notar = price * (purchase_costs_breakdown.get("notar", 0) + purchase_costs_breakdown.get("grundbuch", 0)) / 100
    makler = 0.0 if commission_free else price * purchase_costs_breakdown.get("makler", 0) / 100

    # Year-1 operations
    district_price_sqm = market.get_avg_price_sqm(district)
    ground_rent = erbbauzins_annual(listing, a, district_price_sqm)
    opex, opex_est = annual_operating_costs(listing, annual_rent, a, district_price_sqm)
    noi = annual_rent - opex
    debt_service = monthly_payment * 12
    dscr = (noi / debt_service) if debt_service > 0 else None
    interest_m1 = loan * criteria.interest_rate_pct / 100 / 12
    principal_m1 = max(monthly_payment - interest_m1, 0.0)

    share = a.building_share_house if is_house_like(listing) else a.building_share_apartment
    if listing.is_erbbaurecht:
        share = 1.0  # no land is bought: the whole price is building (depreciable)
    afa_pct = afa_rate_pct(listing.year_built)
    afa_year = total_purchase_cost * share * afa_pct / 100

    # Break-even Kaltmiete per m² for pre-tax CF = 0 (opex scales with rent
    # only via vacancy/admin, so solve linearly).
    break_even = None
    if size > 0:
        fixed_opex, _ = annual_operating_costs(listing, 0.0, a, district_price_sqm)
        rent_linked = a.vacancy_pct / 100 + (a.house_admin_pct_rent / 100 if is_house_like(listing) else 0.0)
        denom = 1 - rent_linked
        if denom > 0:
            break_even = (debt_service + fixed_opex) / denom / 12 / size

    base = bear = bull = None
    if rent_month > 0 and price > 0:
        trend = market.get_district_trend(district)
        g = a.rent_growth_pct.get(trend, 2.0)
        app = a.appreciation_pct.get(trend, 1.5)
        if listing.is_erbbaurecht:
            app += a.erbbau_appreciation_pp
        cap = a.rent_cap_pct_3y.get(market.slug, a.default_rent_cap_pct_3y)
        below_mkt = tenanted and market_rent_month > rent_month
        common = dict(
            a=a, price=price, equity=equity, loan=loan,
            interest_pct=criteria.interest_rate_pct, monthly_payment=monthly_payment,
            rent0_month=rent_month,
            market_rent0_month=market_rent_month if tenanted else rent_month,
            tenanted_below_market=below_mkt, rent_cap_3y=cap,
            afa_year=afa_year, opex0=opex,
        )
        base = _project(name="Basis", rent_growth=g, appreciation=app, **common)
        bear = _project(name="Pessimistisch", rent_growth=g + a.bear_rent_growth_pp,
                        appreciation=app + a.bear_appreciation_pp, **common)
        bull = _project(name="Optimistisch", rent_growth=g + a.bull_rent_growth_pp,
                        appreciation=app + a.bull_appreciation_pp, **common)

    y1 = base.rows[0] if base and base.rows else None
    cf_pre_m = (y1.cf_pre_tax / 12) if y1 else (noi - debt_service) / 12
    tax_m = (y1.tax / 12) if y1 else 0.0
    cf_after_m = (y1.cf_after_tax / 12) if y1 else cf_pre_m

    note = (
        f"Steuersatz {a.tax_rate_pct:.0f} %, AfA {afa_pct:.1f} % auf {share:.0%} Gebäudeanteil, "
        f"Leerstand {a.vacancy_pct:.0f} %, Kosten +{a.cost_inflation_pct:.0f} % p.a., "
        f"Verkaufskosten {a.sale_cost_pct:.0f} %, Verkauf nach {a.hold_years} J"
        + (" (steuerfrei, Spekulationsfrist)" if a.hold_years >= 10 else " (Gewinn versteuert)")
    )

    return InvestmentView(
        rent_month=rent_month,
        rent_sqm=(rent_month / size) if size else 0.0,
        rent_source=rent_source,
        market_rent_month=market_rent_month,
        rent_potential_pct=rent_potential,
        kaufpreisfaktor=kpf,
        district_factor=d_factor,
        fair_value=fair,
        fair_value_gap_pct=fair_gap,
        grunderwerbsteuer=gest,
        notar_grundbuch=notar,
        makler=makler,
        equity=equity,
        loan=loan,
        ltv_pct=(loan / price * 100) if price else 0.0,
        opex_year1=opex,
        opex_estimated=opex_est,
        noi_year1=noi,
        dscr=dscr,
        interest_month_y1=interest_m1,
        principal_month_y1=principal_m1,
        cf_pre_tax_month=cf_pre_m,
        tax_month=tax_m,
        cf_after_tax_month=cf_after_m,
        afa_year=afa_year,
        afa_rate_pct=afa_pct,
        break_even_rent_sqm=break_even,
        hold_years=a.hold_years,
        base=base,
        bear=bear,
        bull=bull,
        assumptions_note=note,
        erbbauzins_year=ground_rent,
        tenanted_rent_unknown=bool(listing.is_rented) and not tenanted,
    )
