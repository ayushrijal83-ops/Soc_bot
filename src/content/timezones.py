"""Audience country -> weighted IANA timezones (offline, from the bundled ``tzdata`` package).

A country is never reduced to one timezone. Countries whose population is spread over several
timezones have a curated table of representative zones with APPROXIMATE population shares
(heuristics for timing suggestions, not audience data). Every other country is derived from
tzdata's ``zone.tab``: its zones are grouped by their UTC offsets in January and July of the given
year (so zones that only differ historically collapse) and the groups get equal weight.

Results depend on the installed tzdata release (``tzdata_version()``): a country that changes its
clocks changes the result. Nothing here touches the network.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from zoneinfo import ZoneInfo

from src.content.audience import ISO_COUNTRIES

# Representative zones -> approximate share of the country's population (each country sums to 1).
# Zones that keep the same clock are folded into one (e.g. Melbourne/Hobart -> Sydney).
CURATED_ZONES: dict[str, tuple[tuple[str, float], ...]] = {
    "US": (("America/New_York", 0.47), ("America/Chicago", 0.29), ("America/Denver", 0.055),
           ("America/Phoenix", 0.02), ("America/Los_Angeles", 0.155), ("America/Anchorage", 0.002),
           ("Pacific/Honolulu", 0.008)),
    "CA": (("America/Toronto", 0.61), ("America/Winnipeg", 0.036), ("America/Regina", 0.03),
           ("America/Edmonton", 0.12), ("America/Vancouver", 0.135), ("America/Halifax", 0.055),
           ("America/St_Johns", 0.014)),
    "AU": (("Australia/Sydney", 0.61), ("Australia/Brisbane", 0.21), ("Australia/Adelaide", 0.07),
           ("Australia/Perth", 0.105), ("Australia/Darwin", 0.005)),
    "RU": (("Europe/Moscow", 0.70), ("Europe/Kaliningrad", 0.01), ("Europe/Samara", 0.03),
           ("Asia/Yekaterinburg", 0.12), ("Asia/Omsk", 0.02), ("Asia/Novosibirsk", 0.05), ("Asia/Irkutsk", 0.02),
           ("Asia/Yakutsk", 0.01), ("Asia/Vladivostok", 0.03), ("Asia/Magadan", 0.005), ("Asia/Kamchatka", 0.005)),
    "BR": (("America/Sao_Paulo", 0.919), ("America/Manaus", 0.075), ("America/Rio_Branco", 0.005),
           ("America/Noronha", 0.001)),
    # Chihuahua moved to Mexico City time in 2022; Ciudad Juarez follows US daylight saving time.
    "MX": (("America/Mexico_City", 0.88), ("America/Tijuana", 0.03), ("America/Ciudad_Juarez", 0.01),
           ("America/Hermosillo", 0.065), ("America/Cancun", 0.015)),
    "ID": (("Asia/Jakarta", 0.80), ("Asia/Makassar", 0.17), ("Asia/Jayapura", 0.03)),
    "ES": (("Europe/Madrid", 0.955), ("Atlantic/Canary", 0.045)),
    "PT": (("Europe/Lisbon", 0.975), ("Atlantic/Azores", 0.025)),
    # zone.tab also lists Asia/Urumqi, which is not China's official civil time.
    "CN": (("Asia/Shanghai", 1.0),),
    # Small outlying islands: equal weights would give them half the country.
    "NZ": (("Pacific/Auckland", 0.999), ("Pacific/Chatham", 0.001)),
    "CL": (("America/Santiago", 0.995), ("America/Punta_Arenas", 0.004), ("Pacific/Easter", 0.001)),
    "EC": (("America/Guayaquil", 0.998), ("Pacific/Galapagos", 0.002)),
}


@dataclass(frozen=True)
class ZoneShare:
    country: str
    zone: str
    weight: float           # share within the country (a country's shares sum to 1)
    source: str             # "curated" | "derived"


def tzdata_version() -> str:
    try:
        import tzdata

        return tzdata.IANA_VERSION
    except (ImportError, AttributeError):
        return "unknown"


@lru_cache(maxsize=1)
def _zone_tab() -> tuple[tuple[str, str], ...]:
    """(country, zone) rows of tzdata's zone.tab, in file order."""
    text = (resources.files("tzdata") / "zoneinfo" / "zone.tab").read_text(encoding="utf-8")
    rows = [line.split("\t") for line in text.splitlines() if line and not line.startswith("#")]
    return tuple((r[0], r[2]) for r in rows)


def _derived(country: str, year: int) -> list[str]:
    """One representative zone per distinct (January, July) UTC offset pair, zone.tab order."""
    groups: dict[tuple, str] = {}
    for code, zone in _zone_tab():
        if code != country:
            continue
        tz = ZoneInfo(zone)
        key = tuple(datetime(year, month, 15, 12, tzinfo=timezone.utc).astimezone(tz).utcoffset() for month in (1, 7))
        groups.setdefault(key, zone)
    return list(groups.values())


def zones_for(countries: list[str], year: int) -> tuple[list[ZoneShare], list[str]]:
    """Weighted zones for each country (in the given order) and notes for the user.

    Raises ValueError for codes that are not ISO 3166-1 alpha-2. Countries without timezone data
    (uninhabited territories such as BV, HM) are left out with a note; nothing is invented.
    """
    invalid = [c for c in countries if c not in ISO_COUNTRIES]
    if invalid:
        raise ValueError(f"Not ISO 3166-1 alpha-2 country codes: {', '.join(invalid)}")
    shares: list[ZoneShare] = []
    notes: list[str] = []
    for country in dict.fromkeys(countries):
        if country in CURATED_ZONES:
            shares += [ZoneShare(country, zone, weight, "curated") for zone, weight in CURATED_ZONES[country]]
            continue
        zones = _derived(country, year)
        if not zones:
            notes.append(f"{country}: no timezone data (uninhabited territory); left out of timing suggestions.")
            continue
        shares += [ZoneShare(country, zone, 1 / len(zones), "derived") for zone in zones]
        if len(zones) > 1:
            notes.append(f"{country}: {len(zones)} timezones from tzdata, weighted equally (no population data).")
    return shares, notes
