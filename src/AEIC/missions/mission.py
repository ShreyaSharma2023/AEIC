# TODO: Remove this when we migrate to Python 3.14+.
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from typing import cast

import pandas as pd

from AEIC.types import Position
from AEIC.utils import GEOD
from AEIC.utils.airports import airport


@dataclass
class Mission:
    """Represents a single flight mission, with all relevant information for
    emissions calculation.

    There are a set of required fields (origin, destination, departure,
    arrival, load factor, and aircraft type) and a set of optional fields
    (carrier, flight number, origin and destination country, service type,
    engine type, seat capacity, and flight ID). The optional fields may be None
    if not available, but are usually filled from fields in the OAG database."""

    origin: str
    """IATA code of origin airport."""

    destination: str
    """IATA code of destination airport."""

    departure: pd.Timestamp
    """Departure time (UTC)."""

    arrival: pd.Timestamp
    """Arrival time (UTC)."""

    aircraft_type: str
    """Aircraft type (ICAO code)."""

    load_factor: float
    """Load factor (between 0 and 1)."""

    carrier: str | None = None
    """Airline (IATA code)."""

    flight_number: str | None = None
    """Flight number."""

    origin_country: str | None = None
    """Origin country (ISO 3166-1 alpha-2 code)."""

    destination_country: str | None = None
    """Destination country (ISO 3166-1 alpha-2 code)."""

    service_type: str | None = None
    """Service type (IATA single-letter code, documented `here
    <https://knowledge.oag.com/v1/docs/iata-service-type-codes>`__)."""

    engine_type: str | None = None
    """Engine type, or None if not known."""

    seat_capacity: int | None = None
    """Seat capacity."""

    flight_id: int | None = None
    """Unique flight ID from mission database (if available)."""

    flight_level: float | None = None
    """Cruise flight level from ADS-B data (FL units), or None if not available."""

    cruise_profile: list[tuple[float, float]] | None = None
    """Observed cruise altitude profile as (cruise_distance_fraction, flight_level)
    breakpoints, ordered by increasing fraction with the first entry at fraction 0.0.
    Sourced from matched or probabilistically-sampled ADS-B step-climb data; None
    falls back to a single constant cruise altitude (from flight_level, or the
    rule-based estimate if that is also unavailable). Fractions are relative to
    this mission's own cruise distance, not the distance of whatever flight the
    profile was drawn from, so a profile can be reused across missions of
    different length."""

    performance_model_key: str | None = None
    """Key identifying the specific performance model TOML file to use for this flight.
    Set during preprocessing from (aircraft_type, engine_type, seat_capacity) lookup.
    Falls back to aircraft_type in the model selector when None."""

    @staticmethod
    def _airport_position(code: str) -> Position:
        ap = airport(code)
        if ap is None:
            raise ValueError(f'Unknown airport code: {code}')
        return ap.position

    @cached_property
    def origin_position(self) -> Position:
        """Spatial position (3-D) of origin airport."""
        return self._airport_position(self.origin)

    @cached_property
    def destination_position(self) -> Position:
        """Spatial position (3-D) of destination airport."""
        return self._airport_position(self.destination)

    @cached_property
    def gc_distance(self) -> float:
        """Great circle distance between departure and arrival positions (m)."""
        return GEOD.inv(
            self.origin_position.longitude,
            self.origin_position.latitude,
            self.destination_position.longitude,
            self.destination_position.latitude,
        )[2]

    @property
    def label(self) -> str:
        """Label for mission, based on origin, destination, and aircraft type."""
        return f'{self.origin}_{self.destination}_{self.aircraft_type}'

    @classmethod
    def from_toml(cls, data: dict) -> list[Mission]:
        """Create a list of `Mission` instances from a TOML-like dictionary.

        This is used for parsing sample mission data.
        """

        result = []
        for f in data['flight']:
            result.append(
                cls(
                    origin=f['origin'],
                    destination=f['destination'],
                    departure=iso_to_timestamp(f['departure']),
                    arrival=iso_to_timestamp(f['arrival']),
                    load_factor=f['load_factor'],
                    aircraft_type=f['aircraft_type'],
                )
            )
        return result


def iso_to_timestamp(s: str) -> pd.Timestamp:
    """Convert an ISO 8601 string to a UTC Pandas `Timestamp`."""
    ts = cast(pd.Timestamp, pd.Timestamp(datetime.fromisoformat(s)))
    if ts.tzinfo is None:
        ts = ts.tz_localize(UTC)
    return ts.tz_convert(UTC)
