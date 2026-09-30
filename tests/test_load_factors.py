"""Tests for the country and month load factor table.

The tables here are small synthetic files: they say nothing about real load
factors, only about how a row is chosen and how bad input is rejected.
"""

import pandas as pd
import pytest

from AEIC.missions import Mission
from AEIC.missions.load_factors import LoadFactorTable

HEADER = (
    'country_code_iso,year,month,domestic_load_factor,domestic_load_factor_basis,'
    'international_load_factor,international_load_factor_basis\n'
)


def table_file(tmp_path, *rows):
    path = tmp_path / 'load_factors.csv'
    path.write_text(HEADER + ''.join(f'{row}\n' for row in rows))
    return path


def mission(origin_country, destination_country, departure='2025-03-10 12:00'):
    return Mission(
        origin='AAA',
        destination='BBB',
        departure=pd.Timestamp(departure, tz='UTC'),
        arrival=pd.Timestamp(departure, tz='UTC') + pd.Timedelta(hours=2),
        aircraft_type='A320',
        load_factor=1.0,
        origin_country=origin_country,
        destination_country=destination_country,
    )


ROWS = (
    'US,23,3,85.0,total_north_america,80.0,international_north_america',
    'US,23,4,70.0,total_north_america,60.0,international_north_america',
    'DE,23,3,75.0,total_europe,90.0,international_europe',
    'FR,23,3,72.0,total_europe,88.0,international_europe',
    'CA,23,3,78.0,total_north_america,66.0,international_north_america',
)


def test_a_domestic_flight_uses_the_domestic_column_as_a_fraction(tmp_path):
    table = LoadFactorTable.load(table_file(tmp_path, *ROWS))

    assert table.load_factor(mission('US', 'US')) == pytest.approx(0.85)


def test_a_flight_between_countries_of_one_basis_is_domestic(tmp_path):
    """DE and FR are both in the table's total_europe basis, so a flight between
    them is domestic and takes the origin's domestic figure, as US to CA does."""
    table = LoadFactorTable.load(table_file(tmp_path, *ROWS))

    assert table.load_factor(mission('DE', 'FR')) == pytest.approx(0.75)
    assert table.load_factor(mission('US', 'CA')) == pytest.approx(0.85)


def test_a_flight_between_countries_of_different_bases_is_international(tmp_path):
    """Uses the origin's international column, not the destination's: DE would
    give 0.90."""
    table = LoadFactorTable.load(table_file(tmp_path, *ROWS))

    assert table.load_factor(mission('US', 'DE')) == pytest.approx(0.80)
    assert table.load_factor(mission('DE', 'US')) == pytest.approx(0.90)


def test_the_month_comes_from_the_departure_time_in_utc(tmp_path):
    table = LoadFactorTable.load(table_file(tmp_path, *ROWS))

    assert table.load_factor(mission('US', 'US', '2025-04-02 00:30')) == pytest.approx(
        0.70
    )


def test_the_year_is_ignored(tmp_path):
    """The table's years are not the simulated year's; only the month counts."""
    table = LoadFactorTable.load(table_file(tmp_path, *ROWS))

    assert table.load_factor(mission('US', 'US', '2031-03-10 12:00')) == pytest.approx(
        0.85
    )


def test_where_a_month_has_several_years_the_latest_is_used(tmp_path):
    table = LoadFactorTable.load(
        table_file(
            tmp_path,
            'US,22,11,50.0,total_north_america,50.0,international_north_america',
            'US,23,11,60.0,total_north_america,60.0,international_north_america',
            'US,21,11,40.0,total_north_america,40.0,international_north_america',
        )
    )

    assert table.load_factor(mission('US', 'US', '2025-11-05 12:00')) == pytest.approx(
        0.60
    )


def test_a_country_or_month_missing_from_the_table_is_an_error(tmp_path):
    table = LoadFactorTable.load(table_file(tmp_path, *ROWS))

    with pytest.raises(ValueError, match='IT.*month 3'):
        table.load_factor(mission('IT', 'IT'))
    with pytest.raises(ValueError, match='destination country IT.*month 3'):
        table.load_factor(mission('US', 'IT'))
    with pytest.raises(ValueError, match='DE.*month 4'):
        table.load_factor(mission('DE', 'DE', '2025-04-10 12:00'))


def test_a_mission_without_an_origin_or_destination_country_is_an_error(tmp_path):
    table = LoadFactorTable.load(table_file(tmp_path, *ROWS))

    with pytest.raises(ValueError, match='origin country'):
        table.load_factor(mission(None, 'US'))
    with pytest.raises(ValueError, match='destination country'):
        table.load_factor(mission('US', None))


@pytest.mark.parametrize('bad', ['0', '-5', '100.5', 'abc', ''])
def test_a_value_outside_zero_to_one_hundred_percent_is_rejected_at_load(tmp_path, bad):
    path = table_file(
        tmp_path, f'US,23,3,{bad},total_north_america,80.0,international_north_america'
    )

    with pytest.raises(ValueError, match='US.*domestic_load_factor'):
        LoadFactorTable.load(path)


def test_an_international_value_outside_range_is_rejected_at_load(tmp_path):
    path = table_file(
        tmp_path, 'US,23,3,85.0,total_north_america,150,international_north_america'
    )

    with pytest.raises(ValueError, match='US.*international_load_factor'):
        LoadFactorTable.load(path)


def test_a_table_missing_a_column_is_an_error(tmp_path):
    path = tmp_path / 'bad.csv'
    path.write_text('country_code_iso,month\nUS,3\n')

    with pytest.raises(ValueError, match='year'):
        LoadFactorTable.load(path)


def test_applying_the_table_replaces_the_missions_load_factor(tmp_path):
    """The placeholder 1.0 from the mission database is what gets replaced, and
    nothing else about the mission changes."""
    table = LoadFactorTable.load(table_file(tmp_path, *ROWS))
    original = mission('US', 'DE')

    applied = table.apply(original)

    assert applied.load_factor == pytest.approx(0.80)
    assert original.load_factor == 1.0
    assert applied.origin == original.origin
    assert applied.departure == original.departure


def test_applying_the_table_to_a_mission_it_has_no_row_for_is_an_error(tmp_path):
    table = LoadFactorTable.load(table_file(tmp_path, *ROWS))

    with pytest.raises(ValueError, match='IT'):
        table.apply(mission('IT', 'IT'))
