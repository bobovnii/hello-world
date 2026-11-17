"""Build a standalone HTML detail report for one search's deals.

The Telegram digest sends short Markdown cards per deal; this module
produces a single self-contained HTML document attached as a Telegram
``document`` after the cards. The HTML carries the full detail a short
card can't fit: every opportunity signal, every red flag, the full
financial breakdown, and the full parameter list per deal.

Design constraints:

- Pure functions, no I/O, no network. Caller passes pre-fetched data.
- Self-contained: doctype, ``<html>``, inline CSS in ``<head>``, no
  external CSS/JS/fonts. Renders offline in any modern browser and in
  Telegram's document preview.
- Safe by default: every user-controlled string is HTML-escaped via
  ``html.escape()``. Any URL used in ``href``/``src`` must start with
  ``http://`` or ``https://``; non-http URLs are replaced with a visible
  placeholder, never rendered as a link or image source. This matters
  because description fields are scraped third-party text that may
  contain ``javascript:`` or ``data:`` payloads.

The German section labels match the existing digest's wording so the
report reads as a continuation of the chat cards.
"""

from __future__ import annotations

import html as _html
from datetime import datetime
from typing import Iterable

from src.database.models import AnalysisResult, Listing


# Visible placeholder for URLs that don't pass the http(s) check.
_SUSPICIOUS_URL_PLACEHOLDER = "[suspicious URL elided]"

# Description hard cap before the trailing ellipsis. Telegram caps the
# document size at 50 MB; this is purely a readability cap.
_DESC_MAX_CHARS = 2000


# ---------------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------------

def _safe_url(url: str | None) -> str | None:
    """Return the URL only if it starts with http:// or https://.

    None / empty / non-http schemes (``javascript:``, ``data:``,
    ``file:``, etc.) all return None. Caller decides how to render the
    rejection (link → placeholder, image → drop entirely).
    """
    if not url:
        return None
    s = url.strip()
    low = s.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return s
    return None


def _esc(value: object) -> str:
    """HTML-escape a value, coercing None to empty string."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def _esc_multiline(value: str | None, max_chars: int = _DESC_MAX_CHARS) -> str:
    """Escape, truncate, then preserve linebreaks as ``<br>``.

    Order matters: escape first (so the ``<br>`` we insert is the only
    raw HTML), truncate after escaping is fine because escape only
    grows the string with safe sequences and we use a generous cap.
    """
    if not value:
        return ""
    text = value
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    escaped = _html.escape(text, quote=True)
    escaped = escaped.replace("\n", "<br>")
    if truncated:
        escaped += "…"
    return escaped


# ---------------------------------------------------------------------------
# Per-deal renderers
# ---------------------------------------------------------------------------

def _split_reasons(reasons: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split undervalue_reasons into (opportunity, red_flag) lists.

    Strings are pre-stripped of their ``[+] `` / ``[!] `` prefix; the
    HTML renders them in distinct sections so the prefix would be
    redundant noise. Strings that match neither prefix go into red
    flags as a conservative default — better to surface an unclassified
    note as a caution than to hide it.
    """
    opportunity: list[str] = []
    redflag: list[str] = []
    for r in reasons or []:
        if r.startswith("[+] "):
            opportunity.append(r[4:])
        elif r.startswith("[+]"):
            opportunity.append(r[3:].lstrip())
        elif r.startswith("[!] "):
            redflag.append(r[4:])
        elif r.startswith("[!]"):
            redflag.append(r[3:].lstrip())
        else:
            redflag.append(r)
    return opportunity, redflag


def _flag_badges(listing: Listing) -> list[tuple[str, str]]:
    """Return a list of (label, css_class) for each true red-flag boolean.

    Stays in sync with the short ``_flags()`` helper in
    ``send_telegram_digest.py`` but adds long German labels suited to
    the standalone HTML view.
    """
    badges: list[tuple[str, str]] = []
    if listing.is_erbbaurecht:
        badges.append(("Erbbaurecht", "badge badge-warn"))
    if listing.is_rented:
        rent = listing.current_rent_monthly
        label = "Vermietet"
        if rent:
            label = f"Vermietet ({rent:,.0f} €/Mo)"
        badges.append((label, "badge badge-warn"))
    if listing.is_wbs:
        badges.append(("WBS / Sozialbindung", "badge badge-warn"))
    if listing.is_dachgeschoss:
        badges.append(("Dachgeschoss", "badge badge-info"))
    if listing.is_ausbau_needed:
        badges.append(("Ausbau erforderlich", "badge badge-warn"))
    er = (listing.energy_rating or "").upper()
    if er in ("F", "G", "H"):
        badges.append((f"Energieklasse {er}", "badge badge-warn"))
    if listing.balcony:
        badges.append(("Balkon", "badge badge-good"))
    if listing.garden:
        badges.append(("Garten", "badge badge-good"))
    if listing.parking:
        badges.append(("Stellplatz", "badge badge-good"))
    return badges


def _time_on_market(listing: Listing) -> str:
    days = listing.days_on_market
    if days <= 0:
        return "Neu im Bot"
    if days == 1:
        return "1 Tag im Markt"
    if days < 60:
        return f"{days} Tage im Markt"
    return f"{days} Tage im Markt — möglicherweise verhandelbar"


def _money(value: float | None, suffix: str = " €") -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}{suffix}"


def _pct(value: float | None, sign: bool = False) -> str:
    if value is None:
        return "—"
    if sign:
        return f"{value:+.1f} %"
    return f"{value:.1f} %"


def _render_kv(rows: list[tuple[str, str]]) -> str:
    """Render a key/value definition list. Rows must be pre-escaped."""
    items = "".join(
        f'<div class="kv"><span class="kv-key">{k}</span>'
        f'<span class="kv-val">{v}</span></div>'
        for k, v in rows
    )
    return f'<div class="kv-grid">{items}</div>'


def _num(value: float | None, fmt: str = "{:.1f}", suffix: str = "") -> str:
    if value is None:
        return "—"
    return fmt.format(value) + suffix


def _render_investor(analysis: AnalysisResult) -> str:
    """Investor metrics + 10-year buy-and-rent scenarios (empty if unavailable)."""
    v = getattr(analysis, "investment", None)
    if v is None or not v.rent_month:
        return ""

    rent_label = "Ist-Kaltmiete / Monat" if v.rent_potential_pct is not None else "Erwartete Kaltmiete / Monat"
    rows: list[tuple[str, str]] = [
        (rent_label, _esc(f"{_money(v.rent_month)} ({v.rent_sqm:.1f} €/m²)")),
        ("Mietquelle", _esc(v.rent_source + (
            " — vermietet, Ist-Miete unbekannt (oft unter Markt)" if v.tenanted_rent_unknown else ""
        ))),
    ]
    if v.erbbauzins_year:
        rows.append(("Erbbauzins / Jahr (geschätzt)", _esc(_money(v.erbbauzins_year))))
    if v.rent_potential_pct is not None:
        rows.append(("Marktmiete / Monat", _esc(_money(v.market_rent_month))))
        rows.append(("Mietsteigerungspotenzial", _esc(_pct(v.rent_potential_pct, sign=True))))
    rows += [
        ("Kaufpreisfaktor", _esc(_num(v.kaufpreisfaktor, "{:.1f}", "×"))),
        ("Faktor Ø Lage", _esc(_num(v.district_factor, "{:.1f}", "×"))),
        ("Grunderwerbsteuer", _esc(_money(v.grunderwerbsteuer))),
        ("Notar + Grundbuch", _esc(_money(v.notar_grundbuch))),
        ("Maklerprovision", _esc(_money(v.makler) if v.makler else "0 € (provisionsfrei)")),
        ("Darlehen / Beleihung", _esc(f"{_money(v.loan)} ({v.ltv_pct:.0f} % vom Kaufpreis)")),
        ("Nicht umlagef. Kosten / Jahr", _esc(_money(v.opex_year1) + (" (geschätzt)" if v.opex_estimated else ""))),
        ("Reinertrag (NOI) / Jahr", _esc(_money(v.noi_year1))),
        ("Schuldendienstdeckung (DSCR)", _esc(_num(v.dscr, "{:.2f}"))),
        ("Zins / Tilgung Monat 1", _esc(f"{_money(v.interest_month_y1)} / {_money(v.principal_month_y1)}")),
        ("Cashflow vor Steuer / Monat", _esc(_money(v.cf_pre_tax_month))),
        ("Steuereffekt / Monat", _esc(_money(-v.tax_month))),
        ("Cashflow nach Steuer / Monat", _esc(_money(v.cf_after_tax_month))),
        ("AfA / Jahr", _esc(f"{_money(v.afa_year)} ({v.afa_rate_pct:.1f} %)")),
        ("Miete für Cashflow 0", _esc(_num(v.break_even_rent_sqm, "{:.1f}", " €/m²"))),
    ]
    html_out = "<h3 class='sub'>Investoren-Kennzahlen</h3>" + _render_kv(rows)

    scen = [s for s in (v.bear, v.base, v.bull) if s is not None]
    if scen:
        head = (
            "<tr><th>Szenario</th><th>Wertzuwachs p.a.</th><th>Mietsteigerung p.a.</th>"
            "<th>IRR vor St.</th><th>IRR nach St.</th><th>EK-Multiple</th>"
            "<th>Gewinn n. St.</th><th>EK bei Verkauf</th></tr>"
        )
        body = "".join(
            "<tr>"
            f"<td>{_esc(s.name)}</td>"
            f"<td>{_esc(_pct(s.appreciation_pct))}</td>"
            f"<td>{_esc(_pct(s.rent_growth_pct))}</td>"
            f"<td>{_esc(_pct(s.irr_pre_tax_pct))}</td>"
            f"<td><b>{_esc(_pct(s.irr_after_tax_pct))}</b></td>"
            f"<td>{_esc(_num(s.equity_multiple, '{:.2f}', '×'))}</td>"
            f"<td>{_esc(_money(s.profit_after_tax))}</td>"
            f"<td>{_esc(_money(s.exit_equity))}</td>"
            "</tr>"
            for s in scen
        )
        html_out += (
            f"<h3 class='sub'>{v.hold_years}-Jahres-Szenario: kaufen &amp; vermieten</h3>"
            f"<table class='tbl'>{head}{body}</table>"
        )
        if v.base and v.base.rows:
            yhead = (
                "<tr><th>Jahr</th><th>Miete</th><th>Kosten</th><th>Zins</th><th>Tilgung</th>"
                "<th>CF vor St.</th><th>Steuer</th><th>CF nach St.</th><th>Restschuld</th><th>Wert</th></tr>"
            )
            ybody = "".join(
                "<tr>"
                f"<td>{r.year}</td><td>{_esc(_money(r.rent))}</td><td>{_esc(_money(r.costs))}</td>"
                f"<td>{_esc(_money(r.interest))}</td><td>{_esc(_money(r.principal))}</td>"
                f"<td>{_esc(_money(r.cf_pre_tax))}</td><td>{_esc(_money(r.tax))}</td>"
                f"<td>{_esc(_money(r.cf_after_tax))}</td><td>{_esc(_money(r.loan_balance))}</td>"
                f"<td>{_esc(_money(r.property_value))}</td>"
                "</tr>"
                for r in v.base.rows
            )
            html_out += (
                "<details><summary>Jahr für Jahr (Basis-Szenario)</summary>"
                f"<table class='tbl'>{yhead}{ybody}</table></details>"
            )
        html_out += f"<p class='note'>Annahmen: {_esc(v.assumptions_note)}.</p>"
    return html_out


def _render_deal(
    listing: Listing,
    analysis: AnalysisResult,
    city_display_name: str = "Hamburg",
) -> str:
    """Render a single deal section. All inputs are escaped here.

    ``city_display_name`` is the human-readable name of the search's
    city (Hamburg / Berlin / Dresden / Heide). Used as the fallback
    label when ``listing.district`` is empty so non-Hamburg cards don't
    silently say "Hamburg" anymore.
    """
    title = _esc(listing.title or "(ohne Titel)")
    district = _esc(listing.district or city_display_name)
    address = _esc(listing.address) if listing.address else ""

    safe_url = _safe_url(listing.url)
    if safe_url:
        link_html = (
            f'<a class="deal-link" href="{_esc(safe_url)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f"{_esc(safe_url)}</a>"
        )
    else:
        link_html = (
            f'<span class="deal-link suspicious">'
            f"{_esc(_SUSPICIOUS_URL_PLACEHOLDER)}</span>"
        )

    # Thumbnails: up to 3 valid http(s) image URLs.
    thumb_html = ""
    valid_imgs = []
    for u in (listing.image_urls or [])[:10]:  # cap upstream just in case
        s = _safe_url(u)
        if s:
            valid_imgs.append(s)
        if len(valid_imgs) >= 3:
            break
    if valid_imgs:
        imgs = "".join(
            f'<img class="thumb" src="{_esc(u)}" alt="" loading="lazy">'
            for u in valid_imgs
        )
        thumb_html = f'<div class="thumbs">{imgs}</div>'

    # Opportunity / red flag lists.
    opps, flags = _split_reasons(analysis.undervalue_reasons or [])
    opps_html = (
        "<ul class='reasons reasons-opp'>"
        + "".join(f"<li>{_esc(r)}</li>" for r in opps)
        + "</ul>"
        if opps
        else "<p class='reasons-empty'>Keine Signale erkannt.</p>"
    )
    flags_html = (
        "<ul class='reasons reasons-flag'>"
        + "".join(f"<li>{_esc(r)}</li>" for r in flags)
        + "</ul>"
        if flags
        else "<p class='reasons-empty'>Keine Red Flags erkannt.</p>"
    )

    # Badges row.
    badges = _flag_badges(listing)
    badge_html = ""
    if badges:
        badge_html = "<div class='badges'>" + "".join(
            f'<span class="{_esc(cls)}">{_esc(label)}</span>'
            for label, cls in badges
        ) + "</div>"

    # Financial KV grid.
    fin_rows: list[tuple[str, str]] = [
        ("Kaufpreis", _esc(_money(listing.price))),
        ("Gesamtkosten (inkl. NK)", _esc(_money(analysis.total_purchase_cost))),
        ("Preis / m²", _esc(_money(analysis.price_per_sqm))),
        ("Marktdurchschnitt €/m²", _esc(_money(analysis.district_avg_price_sqm))),
        ("Abweichung Markt", _esc(_pct(analysis.price_vs_market_pct, sign=True))),
        ("Bruttorendite", _esc(_pct(analysis.gross_rental_yield_pct))),
        ("Nettorendite", _esc(_pct(analysis.net_rental_yield_pct))),
        ("Cap Rate", _esc(_pct(analysis.cap_rate_pct))),
        ("Cashflow / Monat", _esc(_money(analysis.monthly_cashflow))),
        ("Annuität / Monat", _esc(_money(analysis.mortgage_monthly))),
        ("Eigenkapital", _esc(_money(analysis.equity_required))),
        ("Geschätzte Miete / Monat", _esc(_money(analysis.estimated_rent_monthly))),
        ("Score", _esc(f"{analysis.deal_score:.0f}/100")),
    ]

    # Object KV grid.
    obj_rows: list[tuple[str, str]] = [
        ("Bezirk", district),
        ("Adresse", address or "—"),
        ("Zimmer", _esc(f"{listing.rooms:.1f}")),
        ("Wohnfläche", _esc(f"{listing.size_sqm:.0f} m²")),
        ("Baujahr", _esc(listing.year_built) if listing.year_built else "—"),
        ("Hausgeld / Monat", _esc(_money(listing.hausgeld))),
        ("Nebenkosten / Monat", _esc(_money(listing.nebenkosten))),
        ("Energieklasse", _esc(listing.energy_rating or "—")),
        ("Etage", _esc(listing.floor) if listing.floor is not None else "—"),
        ("Plattform", _esc(listing.platform)),
        ("Erstmals gesichtet", _esc(_time_on_market(listing))),
    ]

    investor_html = _render_investor(analysis)

    description_html = ""
    if listing.description:
        description_html = (
            "<h3 class='sub'>Beschreibung</h3>"
            f"<p class='desc'>{_esc_multiline(listing.description)}</p>"
        )

    return (
        f'<section class="deal" data-listing-id="{_esc(listing.id)}">'
        f'<header class="deal-head">'
        f'<h2 class="deal-title">{title}</h2>'
        f'<div class="deal-meta">'
        f'<span class="score">Score {_esc(f"{analysis.deal_score:.0f}")}/100</span>'
        f'<span class="district">{district}</span>'
        f'<span class="price">{_esc(_money(listing.price))}</span>'
        f"</div>"
        f"</header>"
        f"{badge_html}"
        f"{thumb_html}"
        "<h3 class='sub'>Warum dieser Deal günstig sein könnte</h3>"
        f"{opps_html}"
        "<h3 class='sub'>Risiken &amp; Red Flags</h3>"
        f"{flags_html}"
        "<h3 class='sub'>Finanzen</h3>"
        f"{_render_kv(fin_rows)}"
        f"{investor_html}"
        "<h3 class='sub'>Objektdaten</h3>"
        f"{_render_kv(obj_rows)}"
        f"{description_html}"
        f"<p class='deal-link-row'>{link_html}</p>"
        "</section>"
    )


# ---------------------------------------------------------------------------
# Document shell
# ---------------------------------------------------------------------------

_INLINE_CSS = """
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
           Roboto, Helvetica, Arial, sans-serif;
           margin: 0; padding: 1.5rem; background: #f6f6f7; color: #1f2328;
           line-height: 1.45; }
    .doc-head { max-width: 880px; margin: 0 auto 1.5rem; }
    .doc-head h1 { margin: 0 0 .25rem; font-size: 1.6rem; }
    .doc-head .gen { color: #57606a; font-size: .85rem; }
    .deal { max-width: 880px; margin: 0 auto 2rem; padding: 1.25rem 1.5rem;
            background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px; }
    .deal-head { border-bottom: 1px solid #eaeef2; padding-bottom: .5rem;
                 margin-bottom: .75rem; }
    .deal-title { margin: 0 0 .35rem; font-size: 1.2rem; }
    .deal-meta { display: flex; flex-wrap: wrap; gap: .75rem;
                 color: #57606a; font-size: .9rem; }
    .deal-meta .score { background: #ddf4ff; color: #0969da;
                        padding: .1rem .5rem; border-radius: 4px;
                        font-weight: 600; }
    .deal-meta .price { font-weight: 600; color: #1a7f37; }
    .sub { margin: 1rem 0 .35rem; font-size: 1rem; color: #1f2328; }
    .badges { margin: .25rem 0 .75rem; display: flex; flex-wrap: wrap;
              gap: .35rem; }
    .badge { display: inline-block; padding: .15rem .55rem; border-radius: 999px;
             font-size: .8rem; font-weight: 500; }
    .badge-warn { background: #fff8c5; color: #7d4e00; border: 1px solid #d4a72c; }
    .badge-info { background: #ddf4ff; color: #0969da; border: 1px solid #54aeff; }
    .badge-good { background: #dafbe1; color: #1a7f37; border: 1px solid #4ac26b; }
    .thumbs { display: flex; gap: .5rem; margin: .5rem 0 .75rem;
              flex-wrap: wrap; }
    .thumb { width: 180px; height: 120px; object-fit: cover;
             border-radius: 6px; border: 1px solid #d0d7de; background: #eee; }
    .reasons { margin: 0 0 .5rem 1.2rem; padding: 0; }
    .reasons li { margin-bottom: .2rem; }
    .reasons-opp li { color: #1a7f37; }
    .reasons-flag li { color: #9a3412; }
    .reasons-empty { color: #57606a; font-style: italic; margin: 0 0 .5rem; }
    .kv-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
               gap: .25rem .75rem; margin-bottom: .5rem; }
    .kv { display: flex; justify-content: space-between; padding: .15rem 0;
          border-bottom: 1px dashed #eaeef2; font-size: .9rem; }
    .kv-key { color: #57606a; }
    .kv-val { font-variant-numeric: tabular-nums; font-weight: 500; }
    .tbl { width: 100%; border-collapse: collapse; font-size: .85rem;
           font-variant-numeric: tabular-nums; margin: .25rem 0 .5rem;
           display: block; overflow-x: auto; }
    .tbl th, .tbl td { padding: .25rem .5rem; border-bottom: 1px solid #eaeef2;
                       text-align: right; white-space: nowrap; }
    .tbl th:first-child, .tbl td:first-child { text-align: left; }
    .tbl th { color: #57606a; font-weight: 500; }
    details summary { cursor: pointer; color: #0969da; font-size: .9rem; margin: .25rem 0; }
    .note { color: #57606a; font-size: .8rem; margin: .25rem 0 .5rem; }
    .desc { background: #f6f8fa; padding: .75rem 1rem; border-radius: 6px;
            white-space: pre-wrap; font-size: .92rem; }
    .deal-link-row { margin: .9rem 0 0; word-break: break-all; }
    .deal-link { color: #0969da; text-decoration: none; font-size: .9rem; }
    .deal-link:hover { text-decoration: underline; }
    .deal-link.suspicious { color: #9a3412; font-style: italic; }
    .empty { max-width: 880px; margin: 2rem auto; padding: 1.25rem 1.5rem;
             background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px;
             color: #57606a; font-style: italic; }
"""


def build_digest_html(
    search_name: str,
    search_slug: str,
    deals: list[tuple[Listing, AnalysisResult]],
    generated_at: datetime | None = None,
    city_display_name: str = "Hamburg",
) -> str:
    """Render the full HTML detail report for one search.

    Parameters
    ----------
    search_name:
        Human-readable search title shown in the document header. HTML-
        escaped before rendering.
    search_slug:
        Stable slug used in the document title and a hidden ``<meta>``
        tag for tooling. HTML-escaped before rendering.
    deals:
        Sequence of ``(Listing, AnalysisResult)`` tuples. **The caller is
        responsible for pre-sorting by ``analysis.deal_score`` desc.**
        We don't sort defensively here so the caller's intent (e.g. a
        deliberate "newest first" override) is honoured.
    generated_at:
        Timestamp shown in the header. Defaults to ``datetime.now()``.
        Inject for deterministic tests.
    city_display_name:
        Human-readable city name (Hamburg / Berlin / Dresden / Heide).
        Used as the fallback Bezirk label when ``listing.district`` is
        empty AND in the document title. Defaults to "Hamburg" so
        existing single-city callers keep working.

    Returns
    -------
    A complete standalone HTML document (doctype + ``<html>``) that
    runs offline. Every user-controlled value is HTML-escaped; non-http
    URLs are replaced with a visible placeholder rather than rendered.
    """
    if generated_at is None:
        generated_at = datetime.now()

    if deals:
        body_inner = "".join(
            _render_deal(l, a, city_display_name=city_display_name) for l, a in deals
        )
    else:
        body_inner = (
            "<div class='empty'>Keine Deals für diesen Suchlauf — "
            "der Bericht enthält daher keine Details.</div>"
        )

    title_text = f"{city_display_name} Deal-Report — {search_name}"
    return (
        "<!DOCTYPE html>"
        '<html lang="de">'
        "<head>"
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<meta name="report-slug" content="{_esc(search_slug)}">'
        f"<title>{_esc(title_text)}</title>"
        f"<style>{_INLINE_CSS}</style>"
        "</head>"
        "<body>"
        '<div class="doc-head">'
        f"<h1>{_esc(title_text)}</h1>"
        f'<div class="gen">Erstellt am '
        f'{_esc(generated_at.strftime("%Y-%m-%d %H:%M"))} · '
        f'{len(deals)} Deal{"s" if len(deals) != 1 else ""}</div>'
        "</div>"
        f"{body_inner}"
        "</body></html>"
    )
