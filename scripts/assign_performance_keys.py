#!/usr/bin/env python3
"""Assign performance_model_key to flights in the AEIC missions database.

This script reads a CSV mapping file that maps aircraft type codes (and
optionally engine types) to performance model TOML keys, then updates the
flights.performance_model_key column in the given SQLite missions database.

Usage:
    python assign_performance_keys.py --db flights.sqlite \
        --mapping piano_step8_instructions.csv

CSV format:
    The mapping CSV must have at minimum:
        piano_plane_file    - the performance model key (basename of TOML file)
        piano_codes_covered - comma-separated ICAO aircraft type codes this key covers
    Optionally:
        piano_engines_covered - comma-separated engine type strings
                                (empty means match all)
"""

import argparse
import csv
import sqlite3
import sys
from collections import defaultdict


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            'Assign performance_model_key values to flights in the AEIC database.'
        )
    )
    parser.add_argument(
        '--db',
        required=True,
        help='Path to the AEIC SQLite missions database.',
    )
    parser.add_argument(
        '--mapping',
        required=True,
        help='Path to the CSV mapping file.',
    )
    return parser.parse_args()


def load_mapping(mapping_path):
    """Load the mapping from CSV file.

    Returns a list of (piano_plane_file, aircraft_types, engine_types) tuples,
    where engine_types is None if no engine type filter is specified (match all).
    """
    entries = []
    with open(mapping_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            piano_file = row['piano_plane_file'].strip()
            aircraft_types = [
                t.strip() for t in row['piano_codes_covered'].split(',') if t.strip()
            ]
            engine_types = None
            if 'piano_engines_covered' in row and row['piano_engines_covered'].strip():
                engine_types = [
                    e.strip()
                    for e in row['piano_engines_covered'].split(',')
                    if e.strip()
                ]
            for ac_type in aircraft_types:
                entries.append((piano_file, ac_type, engine_types))
    return entries


def assign_keys(db_path, mapping_path):
    """Main logic: load mapping and update the database."""
    entries = load_mapping(mapping_path)

    if not entries:
        print('No entries found in mapping file.')
        sys.exit(1)

    total_updated = 0
    total_unmatched = 0
    per_key_counts = defaultdict(int)

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()

        for piano_file, ac_type, engine_types in entries:
            if engine_types is None:
                # Match all flights with this aircraft_type.
                cur.execute(
                    'UPDATE flights SET performance_model_key = ?'
                    ' WHERE aircraft_type = ?',
                    (piano_file, ac_type),
                )
            else:
                # Match flights with this aircraft_type AND one of the engine types.
                placeholders = ', '.join('?' * len(engine_types))
                cur.execute(
                    f'UPDATE flights SET performance_model_key = ? '
                    f'WHERE aircraft_type = ? AND engine_type IN ({placeholders})',
                    (piano_file, ac_type, *engine_types),
                )
            count = cur.rowcount
            per_key_counts[piano_file] += count
            total_updated += count

        conn.commit()

        # Count flights that still have no performance_model_key.
        cur.execute('SELECT COUNT(*) FROM flights WHERE performance_model_key IS NULL')
        total_unmatched = cur.fetchone()[0]

    print(f'Updated {total_updated} flight(s) with performance_model_key.')
    if per_key_counts:
        print('Breakdown by key:')
        for key, count in sorted(per_key_counts.items()):
            print(f'  {key}: {count}')
    print(f'{total_unmatched} flight(s) have no performance_model_key (unmatched).')


def main():
    args = parse_args()
    assign_keys(args.db, args.mapping)


if __name__ == '__main__':
    main()
