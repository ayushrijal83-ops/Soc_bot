"""Audience Strategy V2.1: country -> weighted IANA timezones (offline, bundled tzdata)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src.content.audience import ISO_COUNTRIES
from src.content.timezones import CURATED_ZONES, tzdata_version, zones_for


def by_country(shares):
    result = {}
    for s in shares:
        result.setdefault(s.country, []).append(s)
    return result


def test_curated_zones_integrity():
    for country, zones in CURATED_ZONES.items():
        assert country in ISO_COUNTRIES
        assert sum(w for _, w in zones) == pytest.approx(1.0)
        assert all(w > 0 for _, w in zones)
        assert len({z for z, _ in zones}) == len(zones)
        for zone, _ in zones:
            assert ZoneInfo(zone).key == zone


def test_every_iso_country_resolves_or_has_a_note():
    shares, notes = zones_for(sorted(ISO_COUNTRIES), 2026)
    grouped = by_country(shares)
    for country in ISO_COUNTRIES:
        if country in grouped:
            assert sum(s.weight for s in grouped[country]) == pytest.approx(1.0), country
        else:
            assert any(n.startswith(f"{country}:") for n in notes), country
    assert set(ISO_COUNTRIES) - set(grouped) == {"BV", "HM"}
    for s in shares:
        ZoneInfo(s.zone)  # every returned zone loads offline
    assert tzdata_version() not in ("", "unknown")


@pytest.mark.parametrize("country", ["US", "CA", "AU", "RU", "BR"])
def test_multi_zone_countries_keep_several_weighted_zones(country):
    shares, notes = zones_for([country], 2026)
    assert len(shares) > 1 and {s.source for s in shares} == {"curated"} and not notes
    assert sum(s.weight for s in shares) == pytest.approx(1.0)
    jan = {datetime(2026, 1, 15, 12, tzinfo=timezone.utc).astimezone(ZoneInfo(s.zone)).utcoffset() for s in shares}
    assert len(jan) > 1  # genuinely different clocks, not one timezone


@pytest.mark.parametrize("country, zone", [("IN", "Asia/Kolkata"), ("JP", "Asia/Tokyo"), ("GB", "Europe/London")])
def test_single_zone_countries(country, zone):
    shares, notes = zones_for([country], 2026)
    assert [(s.zone, s.weight, s.source) for s in shares] == [(zone, 1.0, "derived")] and notes == []


def test_china_is_shanghai_only():
    shares, _ = zones_for(["CN"], 2026)
    assert [(s.zone, s.weight) for s in shares] == [("Asia/Shanghai", 1.0)]


@pytest.mark.parametrize("country", ["BV", "HM"])
def test_uninhabited_territories_get_a_note_not_a_timezone(country):
    shares, notes = zones_for([country, "GB"], 2026)
    assert [s.country for s in shares] == ["GB"]
    assert notes == [f"{country}: no timezone data (uninhabited territory); left out of timing suggestions."]


def test_outlying_islands_do_not_get_half_the_country():
    nz = {s.zone: s.weight for s in zones_for(["NZ"], 2026)[0]}
    assert nz["Pacific/Auckland"] > 0.99 and nz["Pacific/Chatham"] < 0.01


def test_derived_multi_zone_country_is_equal_weight_with_note():
    shares, notes = zones_for(["MN"], 2026)
    assert len(shares) == 2 and {s.source for s in shares} == {"derived"}
    assert all(s.weight == pytest.approx(0.5) for s in shares) and notes[0].startswith("MN: 2 timezones")


def test_invalid_codes_rejected_and_duplicates_collapsed():
    with pytest.raises(ValueError, match="UK"):
        zones_for(["UK"], 2026)
    shares, _ = zones_for(["GB", "GB"], 2026)
    assert len(shares) == 1


def test_resolution_is_reproducible():
    assert zones_for(["MN", "UA", "US"], 2026) == zones_for(["MN", "UA", "US"], 2026)
