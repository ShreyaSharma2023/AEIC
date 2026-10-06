import logging
import random
import sqlite3
import weakref
from collections.abc import Generator
from pathlib import Path
from typing import TypeVar

from .query import QueryBase

logger = logging.getLogger(__name__)


T = TypeVar('T')


_MASK64 = (1 << 64) - 1


def _splitmix64(x: int) -> int:
    """One step of the splitmix64 mixing function: a well-spread 64-bit hash."""
    x = (x + 0x9E3779B97F4A7C15) & _MASK64
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & _MASK64
    return x ^ (x >> 31)


class Database:
    """Flight schedule database.

    Represents a database of flight schedule entries, stored in an SQLite
    database file, using a schema optimized for common AEIC query use cases.
    """

    def __init__(self, db_path: str | Path):
        """Open a flight database file.

        Parameters
        ----------

        db_path : str
            Path to the SQLite database file.
        """

        if isinstance(db_path, str):
            db_path = Path(db_path)

        # Check that the database file exists if we're opening an existing
        # database in read-only mode. Overridden in derived WriteDatabase
        # class.
        self._check_path(db_path)

        # Create connection and ensure it gets closed when the object is
        # collected. (Better to use explicit close or a context manager if
        # possible!)
        self._conn = sqlite3.connect(db_path)
        self._finalizer = weakref.finalize(self, self.close)

        # Create a deterministic random function for use in random sampling
        # queries. SQLite's random() is not reproducible across runs. This is a
        # hash of the row's id and a seed, not a stream of numbers, so whether a
        # row is sampled does not depend on the order SQLite visits rows, on
        # LIMIT and OFFSET or on other conditions, and separate processes with
        # the same seed draw the same sample.
        self._seed_mix = _splitmix64(random.getrandbits(64))

        def det_random(row_id):
            # Like SQLite's random(): a signed 64-bit integer.
            value = _splitmix64((int(row_id) ^ self._seed_mix) & _MASK64)
            return value - 2**64 if value >= 2**63 else value

        self._conn.create_function('det_random', 1, det_random, deterministic=True)

        # Foreign key constraints are enabled at the connection level, so this
        # needs to be done every time we connect to the database.
        self._conn.cursor().execute('PRAGMA foreign_keys = ON')

    def set_random_seed(self, seed: int):
        """Set the seed for deterministic sampling queries: the same seed samples
        the same rows."""
        self._seed_mix = _splitmix64(int(seed) & _MASK64)

    def close(self):
        """Close the database connection."""
        if self._finalizer.alive:
            self._finalizer()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _check_path(self, db_path: Path):
        """Check that the database file exists."""
        if not db_path.exists():
            raise RuntimeError(f'Database file {db_path} does not exist.')

    def __call__(self, query: QueryBase[T]) -> Generator[T, None, None] | T:
        """Execute a query against the database.

        Results are returned via a generator that yields instances of the
        result class for the corresponding query type.

        Supported query types are subclasses of `QueryBase`: `Query` is a
        "normal" scheduled flight query, `FrequentFlightQuery` determines the
        most frequently occurring airport origin/destination pairs, and
        `CountQuery` counts the number of scheduled flights matching filter
        conditions.
        """

        sql, params = query.to_sql()
        cur = self._conn.cursor()

        # Sometimes we return a single result (e.g. for count queries), and
        # sometimes we use a generator to yield multiple results. The single
        # result and generator cases need to be split into separate functions
        # because as soon as Python sees a yield statement in a function, it
        # treats the whole function as a generator.
        if query.PROCESS_RESULT is not None:
            return query.PROCESS_RESULT(cur.execute(sql, params))
        else:
            return self._yield_results(
                cur, sql, params, query.RESULT_CONSTRUCTION_TYPE or query.RESULT_TYPE
            )

    @staticmethod
    def _yield_results(cur, sql, params, result_type: type) -> Generator:
        for row in cur.execute(sql, params):
            yield result_type.from_row(row)
