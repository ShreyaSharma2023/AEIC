"""Load factors by country and month, read from a CSV table.

OAG schedules carry no load factor, so a table supplies one per flight. Each
row gives one country's domestic and international load factors, in percent,
for a month of a year. The columns are ``country_code_iso``, ``year``,
``month``, ``domestic_load_factor`` and ``international_load_factor``, and the
table may have further columns (such as the basis of each figure), which are
ignored.

A flight uses the row of its origin country and the month of its departure.
Each row's ``domestic_load_factor_basis`` names the group the domestic figure
covers (``total_europe``, ``domestic_australia``, ...). A flight whose origin and
destination countries have the same basis is domestic and takes the domestic
column; otherwise it takes the international one. The year of the flight is
ignored: the table's years are not the simulated year's. Where the table has
several years for one country and month, the latest is used.
"""

# TODO: Remove this when we migrate to Python 3.14+.
from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

from .mission import Mission

REQUIRED_COLUMNS = (
    'country_code_iso',
    'year',
    'month',
    'domestic_load_factor',
    'domestic_load_factor_basis',
    'international_load_factor',
)


def _percent(row: dict[str, str], column: str, path: Path) -> float:
    """A load factor column as a fraction, rejecting values outside (0, 100]."""
    where = f'{path}: {row["country_code_iso"]} {row["year"]}-{row["month"]}'
    try:
        value = float(row[column])
    except ValueError:
        raise ValueError(f'{where}: {column} {row[column]!r} is not a number') from None
    if not 0.0 < value <= 100.0:
        raise ValueError(f'{where}: {column} {value:g} is not a percentage in (0, 100]')
    return value / 100.0


class LoadFactorTable:
    """Load factors by country and month, as fractions between 0 and 1."""

    def __init__(self, factors: dict[tuple[str, int], tuple[float, float, str]]):
        self._factors = factors
        """(country, month) -> (domestic, international, domestic basis)."""

    @classmethod
    def load(cls, path: str | Path) -> LoadFactorTable:
        """Read a table from a CSV file.

        Raises:
            ValueError: If a required column is missing or a load factor is
                not a percentage above 0 and at most 100.
        """
        path = Path(path)
        latest: dict[tuple[str, int], int] = {}
        factors: dict[tuple[str, int], tuple[float, float, str]] = {}
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            missing = [
                c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])
            ]
            if missing:
                raise ValueError(f'{path}: missing columns {", ".join(missing)}')
            for row in reader:
                domestic = _percent(row, 'domestic_load_factor', path)
                international = _percent(row, 'international_load_factor', path)
                key = (row['country_code_iso'].strip(), int(row['month']))
                year = int(row['year'])
                if year >= latest.get(key, year):
                    latest[key] = year
                    factors[key] = (
                        domestic,
                        international,
                        row['domestic_load_factor_basis'].strip(),
                    )
        return cls(factors)

    def load_factor(self, mission: Mission) -> float:
        """The load factor for a mission.

        Raises:
            ValueError: If the mission has no origin or destination country, or
                the table has no row for either country in its month.
        """
        if mission.origin_country is None:
            raise ValueError(
                'mission has no origin country to look a load factor up by'
            )
        if mission.destination_country is None:
            raise ValueError(
                'mission has no destination country to tell a domestic flight from '
                'an international one'
            )
        month = mission.departure.month
        domestic, international, basis = self._row(
            mission.origin_country, month, 'origin'
        )
        _, _, destination_basis = self._row(
            mission.destination_country, month, 'destination'
        )
        return domestic if basis == destination_basis else international

    def _row(self, country: str, month: int, role: str) -> tuple[float, float, str]:
        try:
            return self._factors[(country, month)]
        except KeyError:
            raise ValueError(
                f'no load factor for {role} country {country}, month {month}'
            ) from None

    def apply(self, mission: Mission) -> Mission:
        """A copy of the mission with its load factor taken from the table.
        Raises the same errors as `load_factor`."""
        return replace(mission, load_factor=self.load_factor(mission))
