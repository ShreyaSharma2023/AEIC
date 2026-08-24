"""Generate synthetic PIANO-format cruise/climb/descent text fixtures from an
existing legacy performance-model TOML.

This exists so we can validate ``AEIC.parsers.piano_reader`` against files in
the exact format it expects, without needing real (proprietary) PIANO output
in the repo. The source TOML (``sample_performance_model.toml``) is already
public, and the fixtures are round-tripped: (flight_performance table) ->
(synthetic PIANO text) -> (PianoData.load) -> (flight_performance table),
compared for consistency in tests/test_piano_reader.py.

The generator only needs to produce text that ``piano_reader`` can parse
back into equivalent performance data -- it does not need to be a plausible
PIANO export in every cosmetic respect (e.g. NOx/drag columns are filler).
"""

import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

from AEIC.parsers.piano_reader import LB_TO_KG
from AEIC.units import FPM_TO_MPS, KNOTS_TO_MPS

TEST_DIR = Path(__file__).parent.parent / 'tests'
TEST_DATA_DIR = TEST_DIR / 'data' / 'performance' / 'piano'

SOURCE_TOML = (
    Path(__file__).parent.parent
    / 'src'
    / 'AEIC'
    / 'data'
    / 'performance'
    / 'sample_performance_model.toml'
)


def _load_table() -> tuple[pd.DataFrame, dict]:
    """Load and concatenate the climb/cruise/descent flight_performance
    sections into one DataFrame (split apart again downstream by ROCD
    sign, same as the original single-table format)."""
    with open(SOURCE_TOML, 'rb') as f:
        doc = tomllib.load(f)
    frames = []
    for phase in ('climb', 'cruise', 'descent'):
        section = doc[f'{phase}_flight_performance']
        cols = section['cols']
        data = section['data']
        frames.append(pd.DataFrame([row[: len(cols)] for row in data], columns=cols))
    df = pd.concat(frames, ignore_index=True)
    return df, doc


def _integrate_time_and_burn(
    alt_ft: np.ndarray, roc_ms: np.ndarray, fuel_flow_kgs: np.ndarray, descending: bool
) -> tuple[np.ndarray, np.ndarray]:
    """Reconstruct cumulative time [s] and fuel burn [lb] along an altitude
    profile, consistent with the given rate of climb/descent and fuel flow at
    each point (trapezoidal integration), so that
    ``piano_reader._derive_fuel_flow_kgs`` recovers ``fuel_flow_kgs`` from the
    generated ``time_s``/``burn_lb`` columns.
    """
    alt_m = alt_ft * 0.3048
    n = len(alt_ft)
    time_s = np.zeros(n)
    burn_lb = np.zeros(n)
    for i in range(1, n):
        d_alt = abs(alt_m[i] - alt_m[i - 1])
        avg_roc = 0.5 * (abs(roc_ms[i]) + abs(roc_ms[i - 1]))
        dt = d_alt / avg_roc
        avg_ff = 0.5 * (fuel_flow_kgs[i] + fuel_flow_kgs[i - 1])
        time_s[i] = time_s[i - 1] + dt
        burn_lb[i] = burn_lb[i - 1] + (avg_ff * dt) / LB_TO_KG
    return time_s, burn_lb


def _write_climb_file(
    path: Path, df: pd.DataFrame, masses_kg: list[float], speeds: dict
) -> None:
    cas_low_kts = speeds['climb']['cas_low'] / KNOTS_TO_MPS
    cas_high_kts = speeds['climb']['cas_high'] / KNOTS_TO_MPS
    mach = speeds['climb']['mach']
    lines = [
        f' Airspeed schedule   {cas_low_kts:.0f}./ {cas_high_kts:.0f}.kcas/ '
        f'mach {mach:.3f} above 29000.feet',
        '',
    ]
    for mass_kg in masses_kg:
        sub = df[(df['mass'] == mass_kg) & (df['rocd'] > 1e-6)].sort_values('fl')
        alt_ft = (sub['fl'].values * 100.0).astype(float)
        roc_ms = sub['rocd'].values
        ff_kgs = sub['fuel_flow'].values
        time_s, burn_lb = _integrate_time_and_burn(
            alt_ft, roc_ms, ff_kgs, descending=False
        )
        roc_fpm = roc_ms / FPM_TO_MPS

        lines.append(' Climb details')
        lines.append('')
        lines.append(
            '  Alt.    Time     Dist.     Burn     FN/eng   R.o.C.    Drag    NOx'
        )
        lines.append(
            ' (feet)   (sec)  (n.miles)   (lb.)    (lbf.)   (f.p.m)   (lbf.)  (lb.)'
        )
        lines.append('')
        for alt, t, burn, roc in zip(alt_ft, time_s, burn_lb, roc_fpm):
            row = (
                f'{alt:8.1f}{t:9.1f}{0.0:11.1f}{burn:10.1f}'
                f'{0.0:11.1f}{roc:10.1f}{0.0:10.1f}{0.0:9.1f}'
            )
            lines.append(row)
        lines.append('')
    path.write_text('\n'.join(lines) + '\n')


def _write_descent_file(
    path: Path, df: pd.DataFrame, masses_kg: list[float], speeds: dict
) -> None:
    cas_high_kts = speeds['descent']['cas_high'] / KNOTS_TO_MPS
    cas_low_kts = speeds['descent']['cas_low'] / KNOTS_TO_MPS
    mach = speeds['descent']['mach']
    lines = []
    for mass_kg in masses_kg:
        sub = df[(df['mass'] == mass_kg) & (df['rocd'] < -1e-6)].sort_values(
            'fl', ascending=False
        )
        if sub.empty:
            # Not every mass in the source table has descent data (BADA PTF
            # files only report descent at the nominal mass).
            continue
        alt_ft = (sub['fl'].values * 100.0).astype(float)
        rod_ms = -sub['rocd'].values  # positive
        ff_kgs = sub['fuel_flow'].values
        time_s, burn_lb = _integrate_time_and_burn(
            alt_ft, rod_ms, ff_kgs, descending=True
        )
        rod_fpm = rod_ms / FPM_TO_MPS

        mass_lb = mass_kg / LB_TO_KG
        lines.append(f' Descent from FL{int(alt_ft.max() / 100)}')
        lines.append('')
        lines.append(f' Mass {mass_lb:.1f}')
        schedule = f' mach {mach:.3f} above 29000.feet/ {cas_high_kts:.0f}./'
        lines.append(f'{schedule} {cas_low_kts:.0f}.kcas')
        lines.append('')
        lines.append(' Descent details')
        lines.append('')
        lines.append('  Alt.    Time     Dist.     Burn     R.o.D.   FN/eng')
        lines.append(' (feet)   (sec)  (n.miles)   (lb.)    (f.p.m)  (lbf.)')
        lines.append('')
        for alt, t, burn, rod in zip(alt_ft, time_s, burn_lb, rod_fpm):
            row = f'{alt:8.1f}{t:9.1f}{0.0:11.1f}{burn:10.1f}{rod:10.1f}{0.0:11.1f}'
            lines.append(row)
        lines.append('')
    path.write_text('\n'.join(lines) + '\n')


def _write_cruise_file(path: Path, df: pd.DataFrame, design_mach: float) -> None:
    # Columns after "|": TAS CAS Drag MCR% L/D FuelFlow SFC SAR MCLavail
    # RoC@MCL(fixMach) RoC@MCL(fixCAS) -- matching a real PIANO cruise table.
    # The source (sample_performance_model.toml, a BADA-derived legacy
    # fixture) has no real drag/SFC/available-climb-thrust data to draw the
    # last 5 from, so they're simple, structurally-plausible placeholders
    # (level-cruise SFC assumed 0.6 lb/hr/lbf, thrust=drag in cruise, MCL
    # thrust assumed 2x cruise drag) -- fine for round-trip parsing/schema
    # tests, not meant to be physically validated (that happened separately,
    # against real PIANO exports, off-repo).
    assumed_sfc_lbhrlbf = 0.6
    lines = [
        '  Cruise table for B738 (synthetic, from sample_performance_model.toml)',
        '',
        '  Mass   Altitude  Mach   |   TAS    CAS    Drag    MCR.%   L/D  FuelFlow'
        '     SFC      SAR  MCLavail  RoC@MCL  RoC@MCL',
        '  ----   --------  ----   |   ---    ---    ----    -----   --- --------'
        '     ---      ---       ---  fixMach  fixCAS',
        '   lb.     feet    ....   |   kts    kts     lbf.  percent  ...   lb/hr'
        ' lb/h/lbf    nm/lb      lbf.  feet/min feet/min',
        '',
    ]
    cruise = df[df['rocd'].abs() <= 1e-6].sort_values(['mass', 'fl'])
    for row in cruise.itertuples():
        mass_lb = row.mass / LB_TO_KG
        alt_ft = row.fl * 100.0
        tas_kts = row.tas / KNOTS_TO_MPS
        ff_lbhr = (row.fuel_flow / LB_TO_KG) * 3600.0
        drag_lbf = ff_lbhr / assumed_sfc_lbhrlbf
        mcl_avail_lbf = drag_lbf * 2.0
        rocd_mcl_fixmach_fpm = 2000.0
        rocd_mcl_fixcas_fpm = rocd_mcl_fixmach_fpm * 0.9
        data_row = (
            f'  {mass_lb:9.1f}  {alt_ft:8.1f}  {design_mach:.3f}    |    '
            f'{tas_kts:6.1f}  {tas_kts:6.1f}  {drag_lbf:8.1f}  {0.0:8.1f}  '
            f'{0.0:6.2f}  {ff_lbhr:8.1f}  {assumed_sfc_lbhrlbf:.4f}  {0.02:.4f}  '
            f'{mcl_avail_lbf:8.1f}  {rocd_mcl_fixmach_fpm:8.1f}  '
            f'{rocd_mcl_fixcas_fpm:8.1f}'
        )
        lines.append(data_row)
    path.write_text('\n'.join(lines) + '\n')


def main() -> None:
    df, doc = _load_table()
    masses_kg = sorted(df['mass'].unique().tolist())
    speeds = doc['speeds']

    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write_climb_file(TEST_DATA_DIR / 'climb.txt', df, masses_kg, speeds)
    _write_descent_file(TEST_DATA_DIR / 'descent.txt', df, masses_kg, speeds)
    _write_cruise_file(TEST_DATA_DIR / 'cruise.txt', df, speeds['cruise']['mach'])

    print(f'Wrote synthetic PIANO fixtures to {TEST_DATA_DIR}')
    print(f'  Climb/descent masses [kg]: {masses_kg}')
    print(f'  Speeds: {doc["speeds"]}')
    print(f'  Maximum altitude [ft]: {doc["maximum_altitude_ft"]}')


if __name__ == '__main__':
    main()
