"""Filtering flights by performance model key.

A model key names an airframe and engine variant (``B738_01P11CM121``). Selecting
flights by it is how a run flies again only the flights whose performance model
changed, and how a run of the old model set is gridded without them.

The database is the 2019 test subset with keys assigned by flight id: a key is
only a label to the filter, so the keys here are arbitrary.
"""

import shutil
import sqlite3

import pytest

from AEIC.commands.run_simulations import plan_slice
from AEIC.missions import CountQuery, Database, Filter, Query


@pytest.fixture
def keyed_db(tmp_path, test_data_dir):
    path = tmp_path / 'keyed.sqlite'
    shutil.copy(test_data_dir / 'missions/oag-2019-test-subset.sqlite', path)
    con = sqlite3.connect(path)
    # K0, K1, K2 by flight id, and no key at all for every tenth flight.
    con.execute("UPDATE flights SET performance_model_key = 'K' || (id % 3)")
    con.execute('UPDATE flights SET performance_model_key = NULL WHERE id % 10 = 0')
    con.commit()
    con.close()
    return path


def keys_of(db, query):
    return [m.performance_model_key for m in db(query)]


def test_a_filter_keeps_only_flights_with_the_given_keys(keyed_db):
    with Database(keyed_db) as db:
        keys = keys_of(db, Query(filter=Filter(performance_model_key=['K1', 'K2'])))

    assert len(keys) > 0
    assert set(keys) == {'K1', 'K2'}


def test_a_single_key_can_be_given_as_a_string(keyed_db):
    with Database(keyed_db) as db:
        keys = keys_of(db, Query(filter=Filter(performance_model_key='K0')))

    assert set(keys) == {'K0'}


def test_excluding_keys_keeps_every_other_flight_including_those_with_no_key(keyed_db):
    """A flight with no key has no model to have changed, so excluding a key
    must not drop it: SQL's NOT IN would, because NULL is neither in nor not
    in a list."""
    with Database(keyed_db) as db:
        everything = keys_of(db, Query())
        kept = keys_of(db, Query(filter=Filter(exclude_performance_model_key=['K1'])))

    assert None in kept
    assert 'K1' not in kept
    assert len(kept) == len([k for k in everything if k != 'K1'])


def test_including_and_excluding_split_the_flights_exactly(keyed_db):
    """Flown again and not flown again must partition the flights, or a
    reflown flight is counted twice or not at all."""
    with Database(keyed_db) as db:
        total = db(CountQuery())
        chosen = db(CountQuery(filter=Filter(performance_model_key=['K1'])))
        rest = db(CountQuery(filter=Filter(exclude_performance_model_key=['K1'])))

    assert 0 < chosen < total
    assert chosen + rest == total


def test_a_model_key_filter_combines_with_the_other_filters(keyed_db):
    with Database(keyed_db) as db:
        both = keys_of(
            db,
            Query(filter=Filter(performance_model_key=['K1'], min_distance=3000)),
        )
        dists = [m.gc_distance for m in db(Query(filter=Filter(min_distance=3000)))]

    assert set(both) == {'K1'}
    assert 0 < len(both) < len(dists)


def test_a_slice_plan_counts_only_the_filtered_flights(keyed_db):
    """The slices split the flights that match, so a rerun of a few keys is
    not spread thin over every flight."""
    flt = Filter(performance_model_key=['K1'])
    with Database(keyed_db) as db:
        expected = db(CountQuery(filter=flt))
        queries = [plan_slice(db, None, 3, i, filter=flt) for i in range(3)]
        flights = [m for q in queries for m in db(q)]

    assert 0 < expected < 1197
    # Sized for the matching flights, not all 1197: about a third each.
    per_slice = -(-expected // 3)
    assert [q.limit for q in queries] == [
        per_slice,
        per_slice,
        expected - 2 * per_slice,
    ]
    assert len(flights) == expected
    assert len({m.flight_id for m in flights}) == expected
    assert {m.performance_model_key for m in flights} == {'K1'}


def test_a_slice_plan_with_a_filter_and_a_day_uses_both(keyed_db):
    flt = Filter(performance_model_key=['K1'])
    with Database(keyed_db) as db:
        day = next(iter(db(Query(filter=flt)))).departure.date()
        query = plan_slice(db, None, 1, 0, departure_date=day, filter=flt)
        flights = list(db(query))

    assert len(flights) > 0
    assert {m.performance_model_key for m in flights} == {'K1'}
    assert {m.departure.date() for m in flights} == {day}
