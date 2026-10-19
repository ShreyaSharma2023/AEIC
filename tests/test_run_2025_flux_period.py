"""The flux period of the 2025 run's GCHP case files (runs/2025_svante).

GCHP applies a monthly flux over the calendar month. Flights are placed in a
month by UTC departure day, and the two UTC departure days outside 2025
(2024-12-31, 2026-01-01) are folded into January and December so no flight is
lost. The flux must still be divided by the calendar month: dividing by the
32-day span made January and December about 3% low (v2026.10.12).
"""

import calendar
import importlib.util
from datetime import date
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[1] / 'runs' / '2025_svante' / 'build_cases_species_mass.py'
)


@pytest.fixture(scope='module')
def flux_period():
    spec = importlib.util.spec_from_file_location('build_cases_species_mass', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # must not run the build on import
    return module.flux_period


@pytest.mark.parametrize(
    'month, grid_start, grid_end, days, folded',
    [
        (1, '2024-12-31', '2025-01-31', 31, ['2024-12-31']),
        (2, '2025-02-01', '2025-02-28', 28, []),
        (6, '2025-06-01', '2025-06-30', 30, []),
        (12, '2025-12-01', '2026-01-01', 31, ['2026-01-01']),
    ],
)
def test_flux_is_over_the_calendar_month(
    flux_period, month, grid_start, grid_end, days, folded
):
    period = flux_period(month, grid_start, grid_end)
    assert period.start == date(2025, month, 1)
    assert period.seconds == days * 86400.0
    assert [d.isoformat() for d in period.folded_days] == folded


def test_a_year_of_files_covers_365_days(flux_period):
    """The run's grid periods (grid_month.sbatch) give exactly one year."""
    total = 0.0
    for month in range(1, 13):
        start = date(2025, month, 1).isoformat()
        end = date(2025, month, calendar.monthrange(2025, month)[1]).isoformat()
        if month == 1:
            start = '2024-12-31'
        if month == 12:
            end = '2026-01-01'
        total += flux_period(month, start, end).seconds
    assert total == 365 * 86400.0


@pytest.mark.parametrize(
    'month, grid_start, grid_end',
    [
        (1, '2024-12-30', '2025-01-31'),  # two days before
        (2, '2025-01-31', '2025-02-28'),  # an edge day not at the year boundary
        (6, '2025-06-01', '2025-06-29'),  # short of the month
        (12, '2025-12-01', '2026-01-02'),  # two days after
        (3, '2025-02-01', '2025-02-28'),  # another month
    ],
)
def test_any_other_grid_period_is_an_error(flux_period, month, grid_start, grid_end):
    with pytest.raises(ValueError):
        flux_period(month, grid_start, grid_end)
