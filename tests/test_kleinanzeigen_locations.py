"""Verify that hardcoded ``kleinanzeigen_location_id`` values match
kleinanzeigen.de's location-autocomplete endpoint. ROOT-CAUSE GUARD.

Background: location IDs were hardcoded by hand in ``city_registry.py``
based on guesses. Two of four were wrong on first ship — ``l1814`` for
Heide actually pointed to Arnsberg NRW (~500 km away), ``l4030`` for
Dresden actually pointed to Annaberg-Buchholz (~120 km). The user
received a "59759 Arnsberg" listing under the Heide/Lyten search and
was rightly angry.

This module fetches the live autocomplete and asserts each city's
hardcoded ID resolves to the expected place. It is the prevention
layer; ``KleinanzeigenScraper._passes_region_filter`` is the
defense-in-depth layer.

Marked ``network`` so CI / offline runs can opt out via
``pytest -m "not network"``.
"""
from __future__ import annotations

import json

import pytest
import requests

from src.scraper.city_registry import CITIES


_AUTOCOMPLETE_URL = "https://www.kleinanzeigen.de/s-ort-empfehlungen.json"


@pytest.mark.network
@pytest.mark.parametrize(
    "slug, query, expected_label_substring",
    [
        ("hamburg",  "hamburg",  "Hamburg"),
        ("berlin",   "berlin",   "Berlin"),
        ("dresden",  "dresden",  "Dresden - Sachsen"),
        ("heide",    "heide",    "Heide - Dithmarschen"),
    ],
)
def test_hardcoded_location_id_matches_autocomplete(slug, query, expected_label_substring):
    """The location_id we ship with must resolve to the expected place.

    The kleinanzeigen autocomplete returns a JSON object whose keys are
    underscore-prefixed numeric IDs (e.g. ``"_836"``) and values are
    human labels (e.g. ``"Heide - Dithmarschen"``). We assert that our
    hardcoded ``l<id>`` is present AND that its label contains the
    expected city/Bundesland string.
    """
    city = CITIES[slug]
    expected_id = city.kleinanzeigen_location_id
    assert expected_id.startswith("l"), f"id must start with 'l', got {expected_id!r}"
    expected_key = "_" + expected_id[1:]

    resp = requests.get(
        _AUTOCOMPLETE_URL,
        params={"query": query},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    resp.raise_for_status()
    data = json.loads(resp.text)

    assert expected_key in data, (
        f"city {slug!r}: hardcoded id {expected_id} (key {expected_key}) "
        f"is NOT in the autocomplete response for query={query!r}. "
        f"Available keys: {list(data.keys())[:10]}"
    )
    label = data[expected_key]
    assert expected_label_substring.lower() in label.lower(), (
        f"city {slug!r}: id {expected_id} resolves to label {label!r}, "
        f"expected substring {expected_label_substring!r}. "
        f"This is the same class of bug that gave us 'Heide=Arnsberg' — "
        f"someone hardcoded the wrong magic ID. Fix in city_registry.py."
    )
