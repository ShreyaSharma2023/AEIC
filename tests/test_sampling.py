"""Random sampling of scheduled flights, as `aeic run --sample` uses it.

A schedule row is in the sample or not by a deterministic function of its id and the
seed alone. So a sample does not depend on the order SQLite visits rows, on LIMIT and
OFFSET, or on other conditions in the query, which is what lets parallel slices, each
a separate process, agree on one sample.
"""

from datetime import date

import pytest

from AEIC.commands.run_simulations import plan_slice
from AEIC.missions import CountQuery, Database, Filter, Query


@pytest.fixture
def db_file(test_data_dir):
    return test_data_dir / 'missions/oag-2019-test-subset.sqlite'


def sample_ids(db_file, seed, sample=0.2, **query):
    with Database(db_file) as db:
        db.set_random_seed(seed)
        return [m.flight_id for m in db(Query(sample=sample, **query))]


def test_the_same_seed_gives_the_same_sample(db_file):
    assert sample_ids(db_file, 7) == sample_ids(db_file, 7)


def test_a_different_seed_gives_a_different_sample(db_file):
    assert set(sample_ids(db_file, 7)) != set(sample_ids(db_file, 8))


def test_the_sample_is_about_the_fraction_asked_for(db_file):
    n = len(sample_ids(db_file, 7, sample=0.2))

    assert 180 < n < 300  # 1197 rows: 239 expected, standard deviation about 14


def test_pages_of_a_sample_add_up_to_the_whole_sample(db_file):
    """LIMIT and OFFSET must not change which rows are in the sample."""
    whole = sample_ids(db_file, 7)

    pages = []
    for offset in range(0, len(whole), 50):
        pages += sample_ids(db_file, 7, limit=50, offset=offset)

    assert pages == whole


def test_another_condition_selects_from_the_same_sample(db_file):
    """A row's membership does not depend on what else the query asks: the
    sample of the long flights is the long flights of the sample."""
    everything = sample_ids(db_file, 7)
    with Database(db_file) as db:
        long_ids = {m.flight_id for m in db(Query(filter=Filter(min_distance=3000)))}

    long_sample = sample_ids(db_file, 7, filter=Filter(min_distance=3000))

    assert set(long_sample) == set(everything) & long_ids
    assert 0 < len(long_sample) < len(everything)


def test_the_count_of_a_sample_is_the_number_of_rows_it_returns(db_file):
    with Database(db_file) as db:
        db.set_random_seed(7)
        counted = db(CountQuery(sample=0.2))
    assert counted == len(sample_ids(db_file, 7))


def test_slices_of_a_sample_partition_it_exactly(db_file):
    """The slice sizes come from the sample itself, not from an estimate of its
    size, so no sampled flight is dropped and none is flown twice."""
    whole = sample_ids(db_file, 7)

    with Database(db_file) as db:
        queries = [plan_slice(db, 0.2, 4, i, seed=7) for i in range(4)]
    slices = []
    for q in queries:
        assert q is not None
        slices += sample_ids(db_file, 7, limit=q.limit, offset=q.offset)

    assert sorted(slices) == sorted(whole)
    assert len(slices) == len(whole)
    # Sized for the sample (about 60 of 239), not for all 1197 rows.
    assert sum(q.limit for q in queries) == len(whole)
    assert max(q.limit for q in queries) <= -(-len(whole) // 4)


def test_slices_of_a_sample_need_a_seed(db_file):
    """Each slice is a separate process: without a seed each would draw its own
    sample and the slices would not add up to one."""
    with Database(db_file) as db:
        with pytest.raises(ValueError, match='seed'):
            plan_slice(db, 0.2, 4, 0)


def test_a_sample_in_one_day_is_the_sample_of_the_year_restricted_to_that_day(db_file):
    year = sample_ids(db_file, 7)
    day = date(2019, 3, 5)

    in_day = sample_ids(db_file, 7, start_date=day, end_date=day)

    with Database(db_file) as db:
        day_ids = {m.flight_id for m in db(Query(start_date=day, end_date=day))}
    assert set(in_day) == set(year) & day_ids
