import logging
import math
import tomllib
from datetime import date, datetime
from pathlib import Path

import click

import AEIC.trajectories.builders as tb
from AEIC.config import Config, config
from AEIC.emissions import compute_emissions
from AEIC.missions import CountQuery, Database, Query
from AEIC.missions.load_factors import LoadFactorTable
from AEIC.performance.model_selector import (
    PerformanceModelSelector,
    SimplePerformanceModelSelector,
)
from AEIC.performance.models import BasePerformanceModel, PerformanceModel
from AEIC.storage import track_file_accesses
from AEIC.trajectories import TrajectoryStore
from AEIC.types import Fuel
from AEIC.utils.progress import Progress

logger = logging.getLogger(__name__)


def make_trajectory_builder(
    builder: str = 'legacy',
    iterate_mass: bool = False,
    use_weather: bool = False,
    descent_distance_from_model: bool = False,
    iterate_ground_distance: bool = False,
) -> tb.Builder:
    """Make the trajectory builder for a run.

    `legacy` is `LegacyBuilder`, which reproduces the legacy code and only flies
    the legacy (BADA-derived) performance models. `adjustable` is
    `AdjustableLegacyBuilder`, which reproduces it by default but also flies
    other model types, and supports `descent_distance_from_model`. Asking for
    that option with the legacy builder is an error, not something to ignore.
    """
    options = tb.Options(
        iterate_mass=iterate_mass,
        use_weather=use_weather,
        iterate_ground_distance=iterate_ground_distance,
    )
    match builder:
        case 'legacy':
            if descent_distance_from_model:
                raise ValueError(
                    'Estimating the descent distance from the performance model '
                    'needs --builder adjustable: LegacyBuilder reproduces the '
                    'legacy code and does not support it.'
                )
            return tb.LegacyBuilder(options=options)
        case 'adjustable':
            return tb.AdjustableLegacyBuilder(
                options=options,
                legacy_options=tb.AdjustableLegacyOptions(
                    descent_distance_from_model=descent_distance_from_model
                ),
            )
        case _:
            raise ValueError(f'Unknown trajectory builder: {builder!r}')


def plan_slice(
    db: Database,
    sample: float | None,
    slice_count: int,
    slice_index: int,
    departure_date: date | None = None,
) -> Query:
    """The query for the flights of one slice of a run.

    The flights (of the given departure day, if there is one) are split into
    `slice_count` groups of about equal size in departure order, and the query is
    for group `slice_index`. The last group is shortened to fit.

    Raises:
        ValueError: If no flights match. An empty run is far more likely a wrong
            date than an intended result.
    """
    nflights = db(CountQuery(start_date=departure_date, end_date=departure_date))
    assert isinstance(nflights, int)
    if nflights == 0:
        raise ValueError(
            'no flights in the mission database'
            if departure_date is None
            else f'no flights depart on {departure_date}'
        )
    logger.info('Total flights to process: %s', nflights)
    if sample is not None:
        nflights = math.ceil(nflights * sample)
        logger.info('Sampling enabled: %s flights to process after sampling.', nflights)

    # Limit and offset values to use based on slice information. These are used
    # directly in the LIMIT and OFFSET clauses in an SQL query.
    limit = math.ceil(nflights / slice_count)
    offset = limit * slice_index
    if slice_index == slice_count - 1:
        limit = min(limit, nflights - offset)
    logger.info('Flights to process in slice: %s', limit)
    return Query(
        limit=limit,
        offset=offset,
        sample=sample,
        start_date=departure_date,
        end_date=departure_date,
    )


def simulate_slice(
    slice_idx: int,
    sample: float | None,
    seed: int | None,
    query: Query,
    output_store: Path,
    mission_db_file: Path,
    performance_model: BasePerformanceModel | PerformanceModelSelector,
    fuel: Fuel,
    builder: tb.Builder,
    load_factors: LoadFactorTable | None = None,
):
    # There is one output file per slice. We'll merge them into a merged
    # trajectory store when we're done.
    output_file = Path(f'{str(output_store)}-{slice_idx:03d}.nc')
    if output_file.exists():
        output_file.unlink()

    with TrajectoryStore.create(base_file=output_file) as ts:
        with Database(mission_db_file) as db:
            if seed is not None:
                db.set_random_seed(seed)
            if sample is not None or seed is not None:
                ts.set_sampling_info(sample=sample, seed=seed)

            # Record failure information for final report.
            nfailed = 0

            # Retrieve all flights in this slice and simulate them one by one.
            p = Progress(total=query.limit, desc='Flights')
            for mission in db(query):  # type: ignore
                # Fly the mission, catching exceptions.
                try:
                    if load_factors is not None:
                        mission = load_factors.apply(mission)
                    pm = performance_model
                    if isinstance(performance_model, PerformanceModelSelector):
                        pm = performance_model(mission)
                        if pm is None:
                            logger.warning(
                                f'no performance model for mission '
                                f'{mission.origin} -> {mission.destination} '
                                f'({mission.aircraft_type}, '
                                f'{mission.gc_distance / 1000:0.2f} km)'
                            )
                            continue
                    assert isinstance(pm, BasePerformanceModel)
                    traj = builder.fly(pm, mission)
                    traj.add_fields(compute_emissions(pm, fuel, traj))
                except Exception:
                    # General exception from trajectory builder.
                    logger.exception(
                        'Error simulating mission '
                        f'{mission.origin} -> {mission.destination} '
                        f'({mission.aircraft_type}, '
                        f'{mission.gc_distance / 1000:0.2f} km):'
                    )
                    nfailed += 1
                    continue
                finally:
                    p.update()

                # Save the trajectory.
                ts.add(traj)

            p.close()

            # Return information passed back through the process pool future.
            logger.info(f'Slice {slice_idx} complete: {nfailed} failed simulations.')


@click.command(
    short_help='Simulate trajectories for missions in a database.',
    help="""Run trajectory simulations for missions in a database. The missions
    are retrieved from the given database file, and the resulting trajectories
    are saved to a trajectory store. The performance model used for the
    simulations can be specified either as a directory containing multiple
    performance models (one per mission), or as a single performance model file
    to use for all missions. The simulations can be run in parallel by
    splitting the missions into slices, and running each slice separately. The
    output trajectory stores for each slice can be merged together after all
    slices are complete.""",
)
@click.option(
    '--config-file',
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help='Configuration file path.',
)
@click.option(
    '--performance-selector-dir',
    type=click.Path(exists=True, path_type=Path),
    help='Performance model selector path.',
)
@click.option(
    '--performance-model-file',
    type=click.Path(exists=True, path_type=Path),
    help='Performance model path.',
)
@click.option(
    '--mission-db-file',
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help='Mission database path.',
)
@click.option(
    '--output-store',
    type=click.Path(path_type=Path),
    required=True,
    help='Output trajectory store path prefix.',
)
@click.option(
    '--sample',
    type=click.FloatRange(0.0, 1.0),
    help='Fraction of missions to simulate. Must be between 0 and 1.',
)
@click.option(
    '--seed',
    type=int,
    help='Random seed to use when sampling missions.',
)
@click.option(
    '--slice-count', type=int, default=1, help='Number of parallel processing slices.'
)
@click.option(
    '--slice-index',
    type=int,
    default=0,
    help='Index of the slice to process (0-based).',
)
@click.option(
    '--builder',
    'builder_name',
    type=click.Choice(['legacy', 'adjustable']),
    default='legacy',
    show_default=True,
    help='Trajectory builder. The legacy builder only flies legacy performance '
    'models; the adjustable one also flies others, such as PIANO models.',
)
@click.option(
    '--iterate-mass/--no-iterate-mass',
    default=False,
    show_default=True,
    help='Iterate the starting mass until the fuel burned matches the fuel loaded.',
)
@click.option(
    '--descent-distance-from-model',
    is_flag=True,
    help='Estimate the descent distance by flying the descent on the performance '
    'model, instead of the legacy static rule that makes flights land short of '
    'the destination. Needs --builder adjustable.',
)
@click.option(
    '--iterate-ground-distance',
    is_flag=True,
    help='Iterate the descent-distance estimate that decides where cruise ends '
    'until it matches the ground distance actually flown during descent.',
)
@click.option(
    '--load-factor-file',
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help='CSV of load factors by country and month. Without it every flight has '
    'a load factor of 1.0. A flight whose origin country and month are not in '
    'the table fails.',
)
@click.option(
    '--departure-date',
    type=click.DateTime(formats=['%Y-%m-%d']),
    default=None,
    help='Fly only the flights departing on this day (UTC), YYYY-MM-DD. The '
    'slices then split that day\'s flights.',
)
def run_simulations(
    config_file: Path,
    performance_selector_dir: Path | None,
    performance_model_file: Path | None,
    mission_db_file: Path,
    output_store: Path,
    sample: float | None,
    seed: int | None,
    slice_count: int,
    slice_index: int,
    builder_name: str,
    iterate_mass: bool,
    descent_distance_from_model: bool,
    iterate_ground_distance: bool,
    load_factor_file: Path | None,
    departure_date: datetime | None,
):
    if performance_selector_dir is None == performance_model_file is None:
        raise click.UsageError(
            'Exactly one of --performance-selector-dir or '
            '--performance-model-file must be provided.'
        )
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s.%(msecs)03d  %(levelname)s/%(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    logging.captureWarnings(True)

    if slice_count > 1:
        logger.info('Parallel mode: slice %s of %s.', slice_index, slice_count)

    with track_file_accesses():
        # Load given configuration file.
        Config.load(config_file)

        with Database(mission_db_file) as db:
            try:
                query = plan_slice(
                    db,
                    sample,
                    slice_count,
                    slice_index,
                    departure_date.date() if departure_date else None,
                )
            except ValueError as exc:
                raise click.UsageError(str(exc)) from exc

        # Load single performance model to use for all simulations.
        if performance_selector_dir is not None:
            performance_model = SimplePerformanceModelSelector(performance_selector_dir)
        else:
            assert performance_model_file is not None
            performance_model = PerformanceModel.load(performance_model_file)

        # Load fuel data.
        with open(config.emissions.fuel_file, 'rb') as fp:
            fuel = Fuel.model_validate(tomllib.load(fp))

        # Make trajectory builder.
        try:
            builder = make_trajectory_builder(
                builder_name,
                iterate_mass=iterate_mass,
                use_weather=config.weather.use_weather,
                descent_distance_from_model=descent_distance_from_model,
                iterate_ground_distance=iterate_ground_distance,
            )
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc

        simulate_slice(
            slice_index,
            sample,
            seed,
            query,
            output_store,
            mission_db_file,
            performance_model,
            fuel,
            builder,
            LoadFactorTable.load(load_factor_file) if load_factor_file else None,
        )
