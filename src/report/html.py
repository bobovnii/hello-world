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
        return "Neu heute"
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


def _render_deal(listing: Listing, analysis: AnalysisResult) -> str:
    """Render a single deal section. All inputs are escaped here."""
    title = _esc(listing.title or "(ohne Titel)")
    district = _esc(listing.district or "Hamburg")
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

    Returns
    -------
    A complete standalone HTML document (doctype + ``<html>``) that
    runs offline. Every user-controlled value is HTML-escaped; non-http
    URLs are replaced with a visible placeholder rather than rendered.
    """
    if generated_at is None:
        generated_at = datetime.now()

    if deals:
        body_inner = "".join(_render_deal(l, a) for l, a in deals)
    else:
        body_inner = (
            "<div class='empty'>Keine Deals für diesen Suchlauf — "
            "der Bericht enthält daher keine Details.</div>"
        )

    title_text = f"Hamburg Deal-Report — {search_name}"
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
