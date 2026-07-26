"""Extend the wind-off / ERA5 / MERRA2(Np) day-run comparison with the new
`a3dyn` scenario (MERRA2 model levels, properly converted to pressure levels
using real per-column PS -- see merra2_a3dyn_to_pressure_levels.py).

Two things this answers that wind_dayrun_compare.py couldn't:
1. Does a3dyn's aggregate duration/fuel picture match era5/merra2(Np), now
   using MERRA2 data you actually have (vs. Np, which needed a fresh
   download)?
2. What do the 76 flights that FAILED under merra2(Np) -- and so were
   invisible to the original 3-way comparison -- actually look like? These
   are specifically the terrain-crossing routes (Andes/Himalaya/Alps/etc.),
   which is exactly where you'd expect wind effects to be least "typical"
   (mountain waves, channeling) -- so it matters whether dropping them
   silently biased the earlier aggregate.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

OUTPUT_DIR = Path('/net/d16/data/shreya22/Paper_1/wind_phase1_output')
SCENARIOS = ('off', 'era5', 'merra2', 'a3dyn')
LABELS = {
    'off': 'No wind',
    'era5': 'ERA5',
    'merra2': 'MERRA2 (Np)',
    'a3dyn': 'MERRA2 (A3dyn, converted)',
}
COLORS = {'era5': '#C44E52', 'merra2': '#DD8452', 'a3dyn': '#4C72B0'}

scenarios = {}
for name in SCENARIOS:
    df = pd.read_csv(OUTPUT_DIR / f'dayrun_{name}.csv')
    n_total = len(df)
    n_success = int(df['success'].sum())
    print(f'{name}: {n_success}/{n_total} succeeded ({n_success / n_total:.1%})')
    scenarios[name] = df[df['success']].set_index('flight_id')[
        ['total_fuel_mass_kg', 'duration_s']
    ]

# --- 1. Full 4-way comparison, over flights common to ALL scenarios (same
#        859 as before, since merra2/Np is still the limiting scenario). ---
common_ids = scenarios['off'].index
for name in ('era5', 'merra2', 'a3dyn'):
    common_ids = common_ids.intersection(scenarios[name].index)
print(f'\nFlights succeeding in all 4 scenarios: {len(common_ids)} / 935')

merged = scenarios['off'].loc[common_ids].add_suffix('_off')
for name in ('era5', 'merra2', 'a3dyn'):
    merged = merged.join(scenarios[name].loc[common_ids].add_suffix(f'_{name}'))

for name in ('era5', 'merra2', 'a3dyn'):
    merged[f'duration_delta_{name}_min'] = (
        merged[f'duration_s_{name}'] - merged['duration_s_off']
    ) / 60
merged['duration_delta_a3dyn_vs_era5_min'] = (
    merged['duration_s_a3dyn'] - merged['duration_s_era5']
) / 60
merged['duration_delta_a3dyn_vs_merra2_min'] = (
    merged['duration_s_a3dyn'] - merged['duration_s_merra2']
) / 60

merged.to_csv(OUTPUT_DIR / 'dayrun_merged_4way.csv')

summary = pd.DataFrame(
    {
        name: [
            merged[f'total_fuel_mass_kg_{name}'].sum(),
            merged[f'duration_s_{name}'].sum() / 3600,
            merged[f'total_fuel_mass_kg_{name}'].mean(),
            merged[f'duration_s_{name}'].mean() / 60,
        ]
        for name in SCENARIOS
    },
    index=[
        'total_fuel_kg',
        'total_duration_hr',
        'mean_fuel_per_flight_kg',
        'mean_duration_per_flight_min',
    ],
)
for name in ('era5', 'merra2', 'a3dyn'):
    summary[f'{name}_vs_off'] = summary[name] - summary['off']
summary['a3dyn_vs_era5'] = summary['a3dyn'] - summary['era5']
summary['a3dyn_vs_merra2'] = summary['a3dyn'] - summary['merra2']

pd.set_option('display.width', 160)
print()
print(summary.to_string())
summary.to_csv(OUTPUT_DIR / 'dayrun_summary_4way.csv')
print(f'\nSaved {OUTPUT_DIR / "dayrun_summary_4way.csv"}')

print('\nPer-flight duration delta stats (minutes), common 859 flights:')
print(
    merged[
        [
            'duration_delta_era5_min',
            'duration_delta_merra2_min',
            'duration_delta_a3dyn_min',
            'duration_delta_a3dyn_vs_era5_min',
            'duration_delta_a3dyn_vs_merra2_min',
        ]
    ]
    .describe()
    .to_string()
)

r_a3dyn_era5 = merged['duration_delta_era5_min'].corr(
    merged['duration_delta_a3dyn_min']
)
print(f'\nCorrelation(ERA5 delta, A3dyn delta): {r_a3dyn_era5:.4f}')

fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
for ax, (name, title) in zip(
    axes, [('era5', 'ERA5'), ('merra2', 'MERRA2 (Np)'), ('a3dyn', 'MERRA2 (A3dyn)')]
):
    ax.hist(
        merged[f'duration_delta_{name}_min'], bins=40, color=COLORS[name], alpha=0.8
    )
    ax.axvline(0, color='k', linewidth=0.8)
    ax.set_title(f'{title} wind-on vs wind-off\nper-flight duration delta')
    ax.set_xlabel('Duration delta [min]')
fig.tight_layout()
fig.savefig(
    OUTPUT_DIR / 'dayrun_duration_delta_histograms_4way.png',
    dpi=150,
    bbox_inches='tight',
)
plt.close(fig)
print(f'Saved {OUTPUT_DIR / "dayrun_duration_delta_histograms_4way.png"}')

# --- 2. What do the 76 recovered (previously merra2-Np-failed) flights look
#        like? These are specifically the terrain-crossing routes. ---
recovered_ids = (
    scenarios['a3dyn']
    .index.intersection(scenarios['era5'].index)
    .intersection(scenarios['off'].index)
    .difference(scenarios['merra2'].index)
)
print(
    f'\nFlights recovered by a3dyn (failed under merra2/Np, succeed under '
    f'a3dyn): {len(recovered_ids)}'
)

rec = scenarios['off'].loc[recovered_ids].add_suffix('_off')
rec = rec.join(scenarios['era5'].loc[recovered_ids].add_suffix('_era5'))
rec = rec.join(scenarios['a3dyn'].loc[recovered_ids].add_suffix('_a3dyn'))
rec['duration_delta_era5_min'] = (rec['duration_s_era5'] - rec['duration_s_off']) / 60
rec['duration_delta_a3dyn_min'] = (rec['duration_s_a3dyn'] - rec['duration_s_off']) / 60
rec['duration_delta_a3dyn_vs_era5_min'] = (
    rec['duration_s_a3dyn'] - rec['duration_s_era5']
) / 60

rec.to_csv(OUTPUT_DIR / 'dayrun_recovered_flights.csv')
print(
    '\nRecovered-flight duration delta stats (minutes) -- these were '
    'invisible to the original comparison:'
)
print(
    rec[
        [
            'duration_delta_era5_min',
            'duration_delta_a3dyn_min',
            'duration_delta_a3dyn_vs_era5_min',
        ]
    ]
    .describe()
    .to_string()
)

print(
    '\nAll-935-flight comparison (era5 vs off), for context, including '
    'these terrain routes now that a3dyn covers them:'
)
common_era5_mean = merged['duration_delta_era5_min'].mean()
print(f'  Common-859 mean ERA5 delta:     {common_era5_mean:+.3f} min')
rec_era5_mean = rec['duration_delta_era5_min'].mean()
print(f'  Recovered-{len(recovered_ids)} mean ERA5 delta:  {rec_era5_mean:+.3f} min')
rec_a3dyn_mean = rec['duration_delta_a3dyn_min'].mean()
print(f'  Recovered-{len(recovered_ids)} mean A3dyn delta: {rec_a3dyn_mean:+.3f} min')

fig, ax = plt.subplots(figsize=(8, 5.5))
ax.hist(
    merged['duration_delta_era5_min'],
    bins=40,
    color=COLORS['era5'],
    alpha=0.5,
    density=True,
    label='Common 859 flights (ERA5)',
)
ax.hist(
    rec['duration_delta_era5_min'],
    bins=20,
    color='#55A868',
    alpha=0.7,
    density=True,
    label=f'Recovered {len(recovered_ids)} terrain-route flights (ERA5)',
)
ax.axvline(0, color='k', linewidth=0.8)
ax.set_xlabel('Duration delta vs no-wind [min]')
ax.set_ylabel('Density')
ax.set_title('Do the terrain-crossing routes (missed by MERRA2/Np) look different?')
ax.legend(fontsize=9)
fig.tight_layout()
fig.savefig(
    OUTPUT_DIR / 'dayrun_recovered_vs_common_comparison.png',
    dpi=150,
    bbox_inches='tight',
)
plt.close(fig)
print(f'Saved {OUTPUT_DIR / "dayrun_recovered_vs_common_comparison.png"}')
