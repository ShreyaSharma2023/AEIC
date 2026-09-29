"""`aeic make-piano-models`: build PIANO performance models in batch."""

from pathlib import Path

import click

from AEIC.config import Config
from AEIC.performance.piano_batch import read_model_keys, run_batch


@click.command(
    short_help='Build PIANO performance models for every key in a mission database.',
    help="""Build one PIANO performance model TOML for each distinct
    performance model key in a mission database. A key is an airframe and an
    engine, <airframe>_<EDB UID>; the airframe table gives the airframe's
    masses and speeds, and the Emissions Databank gives the engine's LTO data.
    Models that already exist are skipped unless --force is given. The exit
    status is non-zero if any model could not be built.""",
)
@click.option(
    '--mission-db-file',
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help='Mission database to take the performance model keys from.',
)
@click.option(
    '--airframe-table',
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help='CSV with one row per PIANO airframe.',
)
@click.option(
    '--piano-root',
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
    help='Root of the PIANO database (data/performance/<stem>/<stem>_...).',
)
@click.option(
    '--engine-file',
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help='ICAO Emissions Databank workbook.',
)
@click.option(
    '--output-dir',
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help='Directory to write <key>.toml into.',
)
@click.option(
    '--plane-files-dir',
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help='PIANO plane files, to read operating empty mass from when the '
    'airframe table has none.',
)
@click.option('--force', is_flag=True, help='Rebuild models that already exist.')
def make_piano_models(
    mission_db_file,
    airframe_table,
    piano_root,
    engine_file,
    output_dir,
    plane_files_dir,
    force,
):
    Config.load()
    report = run_batch(
        read_model_keys(mission_db_file),
        airframe_table,
        piano_root,
        engine_file,
        output_dir,
        force=force,
        plane_files_dir=plane_files_dir,
    )
    click.echo(
        f'{len(report.built)} built, {len(report.skipped)} skipped, '
        f'{len(report.failed)} failed'
    )
    for key, reason in report.failed.items():
        click.echo(f'FAILED {key}: {reason}', err=True)
    if report.failed:
        raise SystemExit(1)
