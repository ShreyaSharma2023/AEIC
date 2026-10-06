"""`aeic make-piano-models`: build PIANO performance models in batch."""

from pathlib import Path

import click

from AEIC.config import Config
from AEIC.performance.piano_batch import read_model_keys, read_table_keys, run_batch


@click.command(
    short_help='Build PIANO performance models for a set of keys.',
    help="""Build one PIANO performance model TOML for each performance model
    key of a mission database (--mission-db-file) or of an airframe table that
    has one row per key (--keys-from-table). A key is an airframe and an
    engine, <airframe>_<EDB UID>; the airframe table gives the airframe's
    masses and speeds, and the Emissions Databank gives the engine's LTO data.
    Models that already exist are skipped unless --force is given. The exit
    status is non-zero if any model could not be built.""",
)
@click.option(
    '--mission-db-file',
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help='Mission database to take the performance model keys from.',
)
@click.option(
    '--keys-from-table',
    is_flag=True,
    help='Take the keys from the airframe table, which then has one row per key '
    '(columns performance_model_key and edb_uid), instead of a mission database.',
)
@click.option(
    '--sn-override-file',
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help='CSV of smoke numbers by engine UID (uid, sn_idle, sn_app, sn_co, sn_to) '
    'that replace the Emissions Databank\'s. An engine with neither nvPM '
    'measurements nor a full set of smoke numbers is not built.',
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
    keys_from_table,
    sn_override_file,
    airframe_table,
    piano_root,
    engine_file,
    output_dir,
    plane_files_dir,
    force,
):
    if keys_from_table == (mission_db_file is not None):
        raise click.UsageError(
            'Give exactly one of --mission-db-file and --keys-from-table.'
        )
    Config.load()
    keys = (
        read_table_keys(airframe_table)
        if keys_from_table
        else read_model_keys(mission_db_file)
    )
    report = run_batch(
        keys,
        airframe_table,
        piano_root,
        engine_file,
        output_dir,
        force=force,
        plane_files_dir=plane_files_dir,
        sn_override_file=sn_override_file,
    )
    click.echo(
        f'{len(report.built)} built, {len(report.skipped)} skipped, '
        f'{len(report.failed)} failed'
    )
    for key, reason in report.failed.items():
        click.echo(f'FAILED {key}: {reason}', err=True)
    if report.failed:
        raise SystemExit(1)
