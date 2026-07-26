# Does the final ground distance covered (the trajectory's cumulative
# ground_distance at touchdown) differ under no-wind / ERA5 / MERRA2?
#
# Physical reasoning (confirmed by reading legacy.py): cruise always targets
# a fixed distance (`ground_track.total_distance - descent_dist_approx`,
# geometry + altitude only, wind-independent) via `fly_cruise`'s `end_dist`,
# so descent always STARTS from the same ground_distance regardless of wind
# -- climb-phase wind variability gets absorbed by cruise converging back to
# that fixed target. So any wind-driven difference in final ground distance
# should come almost entirely from the descent phase, where actual ground
# speed (wind-dependent) determines how much ground distance is covered
# while losing altitude -- exactly the mechanism behind the GroundTrack
# overstep bug fixed earlier (see wind-groundtrack-bug-explain.py).

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pyproj import Geod

OUTPUT_DIR = Path('/net/d16/data/shreya22/Paper_1/wind_phase1_output')
AIRPORTS_PATH = '/home/shreya22/AEIC_3.0/src/AEIC/data/airports/airports.csv'

COLOR_OFF = '#888888'
COLOR_ERA5 = '#C44E52'
COLOR_MERRA2 = '#4C72B0'
CAT_COLORS = {'Same continent': '#4C72B0', 'Intercontinental': '#DD8452'}

scenarios = {}
for name in ('off', 'era5', 'merra2'):
    df = pd.read_csv(OUTPUT_DIR / f'dayrun_{name}.csv')
    scenarios[name] = df[df['success']].set_index('flight_id')[
        ['final_ground_distance_km']
    ]

common_ids = scenarios['off'].index
for name in ('era5', 'merra2'):
    common_ids = common_ids.intersection(scenarios[name].index)
print(f'Flights succeeding in all 3 scenarios: {len(common_ids)} / 935')

merged = scenarios['off'].loc[common_ids].add_suffix('_off')
for name in ('era5', 'merra2'):
    merged = merged.join(scenarios[name].loc[common_ids].add_suffix(f'_{name}'))
merged = merged.reset_index()

manifest = pd.read_csv(OUTPUT_DIR / 'dayrun_manifest.csv')
merged = merged.merge(manifest[['flight_id', 'origin', 'destination']], on='flight_id')

airports = pd.read_csv(AIRPORTS_PATH)[
    ['iata_code', 'latitude_deg', 'longitude_deg', 'continent']
]
airports = (
    airports.dropna(subset=['iata_code'])
    .drop_duplicates('iata_code')
    .set_index('iata_code')
)
merged = merged.join(airports.add_prefix('origin_'), on='origin')
merged = merged.join(airports.add_prefix('dest_'), on='destination')
merged = merged.dropna(subset=['origin_latitude_deg', 'dest_latitude_deg'])

geod = Geod(ellps='WGS84')
_, _, dist_m = geod.inv(
    merged['origin_longitude_deg'].values,
    merged['origin_latitude_deg'].values,
    merged['dest_longitude_deg'].values,
    merged['dest_latitude_deg'].values,
)
merged['great_circle_km'] = dist_m / 1000.0
merged['route_type'] = np.where(
    merged['origin_continent'] == merged['dest_continent'],
    'Same continent',
    'Intercontinental',
)

for name in ('off', 'era5', 'merra2'):
    merged[f'overshoot_km_{name}'] = (
        merged[f'final_ground_distance_km_{name}'] - merged['great_circle_km']
    )

merged['gd_delta_era5_km'] = (
    merged['final_ground_distance_km_era5'] - merged['final_ground_distance_km_off']
)
merged['gd_delta_merra2_km'] = (
    merged['final_ground_distance_km_merra2'] - merged['final_ground_distance_km_off']
)
merged['gd_delta_merra2_vs_era5_km'] = (
    merged['final_ground_distance_km_merra2'] - merged['final_ground_distance_km_era5']
)

merged.to_csv(OUTPUT_DIR / 'dayrun_ground_distance_merged.csv', index=False)
print('\nOvershoot (final ground distance - great-circle distance) [km], by scenario:')
print(
    merged[['overshoot_km_off', 'overshoot_km_era5', 'overshoot_km_merra2']]
    .describe()
    .to_string()
)

# ---------------------------------------------------------------------------
# Figure 1: overshoot distribution per scenario -- the headline figure. Under
# no wind, overshoot should be small and one-sided (only from the discrete
# altitude-step granularity); under wind it should be wider and can go
# strongly negative (headwind: descent ends short of the great-circle
# distance) or positive (tailwind: overshoot, the case that used to crash).
# ---------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(9, 5.5))
data = [
    merged['overshoot_km_off'],
    merged['overshoot_km_era5'],
    merged['overshoot_km_merra2'],
]
labels = ['No wind', 'ERA5', 'MERRA2']
colors = [COLOR_OFF, COLOR_ERA5, COLOR_MERRA2]
bp = ax.boxplot(
    data, tick_labels=labels, patch_artist=True, showfliers=True, widths=0.55
)
for patch, color in zip(bp['boxes'], colors):
    patch.set_facecolor(color)
    patch.set_alpha(0.6)
for median in bp['medians']:
    median.set_color('black')
ax.axhline(0, color='black', linewidth=0.8, linestyle=':')
ax.set_ylabel(
    'Final ground distance - great-circle distance [km]\n(descent "overshoot")'
)
ax.set_title('Ground-distance overshoot at end of trajectory, by wind condition')
ax.spines[['top', 'right']].set_visible(False)
fig.tight_layout()
fig.savefig(
    OUTPUT_DIR / 'ground_distance_overshoot_boxplot.png', dpi=150, bbox_inches='tight'
)
plt.close(fig)
print('Saved ground_distance_overshoot_boxplot.png')

# ---------------------------------------------------------------------------
# Figure 2: MERRA2 vs ERA5 agreement on ground-distance delta (vs no-wind),
# same style as the duration agreement scatter.
# ---------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(7, 7))
lims = [
    min(merged['gd_delta_era5_km'].min(), merged['gd_delta_merra2_km'].min()) - 2,
    max(merged['gd_delta_era5_km'].max(), merged['gd_delta_merra2_km'].max()) + 2,
]
ax.plot(
    lims,
    lims,
    color='#888888',
    linewidth=1,
    linestyle='--',
    zorder=1,
    label='Perfect agreement (y=x)',
)
ax.scatter(
    merged['gd_delta_era5_km'],
    merged['gd_delta_merra2_km'],
    s=14,
    color=COLOR_MERRA2,
    alpha=0.55,
    edgecolor='none',
    zorder=2,
)
r = np.corrcoef(merged['gd_delta_era5_km'], merged['gd_delta_merra2_km'])[0, 1]
ax.text(
    0.03,
    0.97,
    f'r = {r:.4f}\nn = {len(merged)}',
    transform=ax.transAxes,
    va='top',
    fontsize=11,
    bbox=dict(boxstyle='round', facecolor='white', edgecolor='#cccccc'),
)
ax.set_xlim(lims)
ax.set_ylim(lims)
ax.set_xlabel('ERA5 ground-distance delta vs no-wind [km]')
ax.set_ylabel('MERRA2 ground-distance delta vs no-wind [km]')
ax.set_title(
    'MERRA2 vs ERA5: per-flight agreement on wind-driven\nfinal ground-distance change'
)
ax.legend(loc='lower right', fontsize=9)
ax.spines[['top', 'right']].set_visible(False)
fig.tight_layout()
fig.savefig(
    OUTPUT_DIR / 'ground_distance_merra2_era5_agreement_scatter.png',
    dpi=150,
    bbox_inches='tight',
)
plt.close(fig)
print('Saved ground_distance_merra2_era5_agreement_scatter.png')

# ---------------------------------------------------------------------------
# Figure 3: discrepancy vs route length / vs wind effect size (two panels).
# ---------------------------------------------------------------------------
merged['abs_gd_discrepancy_km'] = merged['gd_delta_merra2_vs_era5_km'].abs()
merged['abs_era5_gd_effect_km'] = merged['gd_delta_era5_km'].abs()

fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
for route_type, color in CAT_COLORS.items():
    sub = merged[merged['route_type'] == route_type]
    axes[0].scatter(
        sub['great_circle_km'],
        sub['gd_delta_merra2_vs_era5_km'],
        s=16,
        color=color,
        alpha=0.6,
        edgecolor='none',
        label=route_type,
    )
axes[0].axhline(0, color='black', linewidth=0.8)
axes[0].set_xlabel('Great-circle route distance [km]')
axes[0].set_ylabel('MERRA2 - ERA5 ground-distance delta [km]')
axes[0].set_title('Discrepancy vs. route length')
axes[0].legend(fontsize=9, loc='upper right')
axes[0].spines[['top', 'right']].set_visible(False)

for route_type, color in CAT_COLORS.items():
    sub = merged[merged['route_type'] == route_type]
    axes[1].scatter(
        sub['abs_era5_gd_effect_km'],
        sub['abs_gd_discrepancy_km'],
        s=16,
        color=color,
        alpha=0.6,
        edgecolor='none',
        label=route_type,
    )
axes[1].set_xlabel('|ERA5 ground-distance effect| (vs no-wind) [km]')
axes[1].set_ylabel('|MERRA2 - ERA5| discrepancy [km]')
axes[1].set_title('Discrepancy vs. size of the wind effect itself')
axes[1].legend(fontsize=9, loc='upper right')
axes[1].spines[['top', 'right']].set_visible(False)

fig.tight_layout()
fig.savefig(
    OUTPUT_DIR / 'ground_distance_discrepancy_drivers.png', dpi=150, bbox_inches='tight'
)
plt.close(fig)
print('Saved ground_distance_discrepancy_drivers.png')

gd_dist_corr = merged['abs_gd_discrepancy_km'].corr(merged['great_circle_km'])
print(f'\nCorrelation(|gd discrepancy|, distance): {gd_dist_corr:.3f}')
gd_era5_corr = merged['abs_gd_discrepancy_km'].corr(merged['abs_era5_gd_effect_km'])
print(f'Correlation(|gd discrepancy|, |ERA5 gd effect|): {gd_era5_corr:.3f}')

duration = pd.read_csv(OUTPUT_DIR / 'dayrun_merged.csv').set_index('flight_id')
joined = merged.set_index('flight_id')[['gd_delta_era5_km']].join(
    duration[['duration_delta_era5_min']]
)
gd_duration_corr = joined['gd_delta_era5_km'].corr(joined['duration_delta_era5_min'])
print(f'Correlation(gd delta, duration delta) [ERA5]: {gd_duration_corr:.3f}')
