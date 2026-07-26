"""Aggregate the wind-off / ERA5 / MERRA2 day-run results (see
wind_dayrun_run_scenario.py) into a single comparison: total/mean fuel and
duration per scenario, pairwise deltas, and the distribution of per-flight
duration deltas (to see whether headwind/tailwind effects cancel out in
aggregate, as expected for a broad, multi-direction flight sample -- unlike
the single cherry-picked JFK<->LAX route from Phase 1)."""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

OUTPUT_DIR = Path('/net/d16/data/shreya22/Paper_1/wind_phase1_output')

scenarios = {}
for name in ('off', 'era5', 'merra2'):
    df = pd.read_csv(OUTPUT_DIR / f'dayrun_{name}.csv')
    n_total = len(df)
    n_success = int(df['success'].sum())
    print(f'{name}: {n_success}/{n_total} succeeded ({n_success / n_total:.1%})')
    scenarios[name] = df[df['success']].set_index('flight_id')[
        ['total_fuel_mass_kg', 'duration_s']
    ]

# Only compare flights that succeeded in all three scenarios.
common_ids = scenarios['off'].index
for name in ('era5', 'merra2'):
    common_ids = common_ids.intersection(scenarios[name].index)
print(f'\nFlights succeeding in all 3 scenarios: {len(common_ids)} / 935')

merged = scenarios['off'].loc[common_ids].add_suffix('_off')
for name in ('era5', 'merra2'):
    merged = merged.join(scenarios[name].loc[common_ids].add_suffix(f'_{name}'))

merged['duration_delta_era5_min'] = (
    merged['duration_s_era5'] - merged['duration_s_off']
) / 60
merged['duration_delta_merra2_min'] = (
    merged['duration_s_merra2'] - merged['duration_s_off']
) / 60
merged['duration_delta_merra2_vs_era5_min'] = (
    merged['duration_s_merra2'] - merged['duration_s_era5']
) / 60
merged['fuel_delta_era5_kg'] = (
    merged['total_fuel_mass_kg_era5'] - merged['total_fuel_mass_kg_off']
)
merged['fuel_delta_merra2_kg'] = (
    merged['total_fuel_mass_kg_merra2'] - merged['total_fuel_mass_kg_off']
)

merged.to_csv(OUTPUT_DIR / 'dayrun_merged.csv')

summary = pd.DataFrame(
    {
        'off': [
            merged['total_fuel_mass_kg_off'].sum(),
            merged['duration_s_off'].sum() / 3600,
            merged['total_fuel_mass_kg_off'].mean(),
            merged['duration_s_off'].mean() / 60,
        ],
        'era5': [
            merged['total_fuel_mass_kg_era5'].sum(),
            merged['duration_s_era5'].sum() / 3600,
            merged['total_fuel_mass_kg_era5'].mean(),
            merged['duration_s_era5'].mean() / 60,
        ],
        'merra2': [
            merged['total_fuel_mass_kg_merra2'].sum(),
            merged['duration_s_merra2'].sum() / 3600,
            merged['total_fuel_mass_kg_merra2'].mean(),
            merged['duration_s_merra2'].mean() / 60,
        ],
    },
    index=[
        'total_fuel_kg',
        'total_duration_hr',
        'mean_fuel_per_flight_kg',
        'mean_duration_per_flight_min',
    ],
)
summary['era5_vs_off'] = summary['era5'] - summary['off']
summary['merra2_vs_off'] = summary['merra2'] - summary['off']
summary['merra2_vs_era5'] = summary['merra2'] - summary['era5']

pd.set_option('display.width', 140)
print()
print(summary.to_string())
summary.to_csv(OUTPUT_DIR / 'dayrun_summary.csv')
print(f'\nSaved {OUTPUT_DIR / "dayrun_summary.csv"}')

print('\nPer-flight duration delta stats (minutes):')
print(
    merged[
        [
            'duration_delta_era5_min',
            'duration_delta_merra2_min',
            'duration_delta_merra2_vs_era5_min',
        ]
    ]
    .describe()
    .to_string()
)

fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
axes[0].hist(merged['duration_delta_era5_min'], bins=40, color='tab:red', alpha=0.8)
axes[0].axvline(0, color='k', linewidth=0.8)
axes[0].set_title('ERA5 wind-on vs wind-off\nper-flight duration delta')
axes[0].set_xlabel('Duration delta [min]')

axes[1].hist(merged['duration_delta_merra2_min'], bins=40, color='tab:blue', alpha=0.8)
axes[1].axvline(0, color='k', linewidth=0.8)
axes[1].set_title('MERRA2 wind-on vs wind-off\nper-flight duration delta')
axes[1].set_xlabel('Duration delta [min]')

axes[2].hist(
    merged['duration_delta_merra2_vs_era5_min'], bins=40, color='tab:purple', alpha=0.8
)
axes[2].axvline(0, color='k', linewidth=0.8)
axes[2].set_title('MERRA2 vs ERA5\nper-flight duration delta')
axes[2].set_xlabel('Duration delta [min]')

fig.tight_layout()
out_png = OUTPUT_DIR / 'dayrun_duration_delta_histograms.png'
fig.savefig(out_png, dpi=150, bbox_inches='tight')
print(f'Saved {out_png}')
