"""`aeic make-weather-data`: convert reanalysis winds to the files `Weather` reads."""

from datetime import date, datetime
from pathlib import Path

import click

from AEIC.config import Config
from AEIC.weather_conversion.merra2 import convert_range, verify_day


@click.group(short_help='Convert reanalysis winds to AEIC weather files.')
def make_weather_data():
    pass


@make_weather_data.command(
    help="""Convert MERRA2 winds (the A3dyn and I3 files of GEOS-Chem's
    GEOS_0.5x0.625/MERRA2 tree) to daily files of hourly winds on fixed pressure
    levels, named YYYY-MM-DD.nc. Use them with file_resolution = "daily" and
    data_resolution = "hourly" in the configuration. Levels inside the ground
    get zero wind. Each day needs the previous and next day's files as well.
    Days that already exist are skipped unless --force is given, and days are
    independent, so a range can be split across jobs.""",
)
@click.option(
    '--source-dir',
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help='Root of the GEOS_0.5x0.625/MERRA2 tree.',
)
@click.option(
    '--output-dir',
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help='Directory to write the daily files into.',
)
@click.option(
    '--start',
    type=click.DateTime(['%Y-%m-%d']),
    required=True,
    help='First day, YYYY-MM-DD.',
)
@click.option(
    '--end',
    type=click.DateTime(['%Y-%m-%d']),
    required=True,
    help='Last day, YYYY-MM-DD.',
)
@click.option(
    '--source-year',
    type=int,
    help='Read this year\'s MERRA2 files instead, labelling the result with the '
    'day\'s own year. Stands in for a year whose files are not available.',
)
@click.option('--force', is_flag=True, help='Rewrite days that already exist.')
@click.option(
    '--verify',
    is_flag=True,
    help='Read each written day back through the Weather reader.',
)
def merra2(source_dir, output_dir, start, end, source_year, force, verify):
    Config.load()
    first, last = _as_date(start), _as_date(end)
    if last < first:
        raise click.UsageError('--end is before --start.')
    report = convert_range(
        source_dir, output_dir, first, last, source_year=source_year, force=force
    )
    if verify:
        for path in report.written:
            verify_day(output_dir, datetime.strptime(path.stem, '%Y-%m-%d').date())
    click.echo(f'{len(report.written)} written, {len(report.skipped)} skipped')


def _as_date(value: datetime) -> date:
    return value.date()
