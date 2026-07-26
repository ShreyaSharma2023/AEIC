# Deeper look at MERRA2 vs ERA5 specifically (not vs no-wind) across the
# 859-flight day-run comparison. Joins in great-circle distance (via airport
# lat/lon + pyproj) and continent pairing to look for patterns in where/when
# the two wind sources disagree.

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pyproj import Geod

OUTPUT_DIR = Path('/net/d16/data/shreya22/Paper_1/wind_phase1_output')
AIRPORTS_PATH = '/home/shreya22/AEIC_3.0/src/AEIC/data/airports/airports.csv'

merged = pd.read_csv(OUTPUT_DIR / 'dayrun_merged.csv')
manifest = pd.read_csv(OUTPUT_DIR / 'dayrun_manifest.csv')
df = merged.merge(
    manifest[['flight_id', 'origin', 'destination', 'aircraft_type']], on='flight_id'
)

airports = pd.read_csv(AIRPORTS_PATH)[
    ['iata_code', 'latitude_deg', 'longitude_deg', 'continent']
]
airports = (
    airports.dropna(subset=['iata_code'])
    .drop_duplicates('iata_code')
    .set_index('iata_code')
)

df = df.join(airports.add_prefix('origin_'), on='origin')
df = df.join(airports.add_prefix('dest_'), on='destination')
df = df.dropna(subset=['origin_latitude_deg', 'dest_latitude_deg'])

geod = Geod(ellps='WGS84')
_, _, dist_m = geod.inv(
    df['origin_longitude_deg'].values,
    df['origin_latitude_deg'].values,
    df['dest_longitude_deg'].values,
    df['dest_latitude_deg'].values,
)
df['great_circle_km'] = dist_m / 1000.0

df['same_continent'] = df['origin_continent'] == df['dest_continent']
df['route_type'] = np.where(df['same_continent'], 'Same continent', 'Intercontinental')

# ---------------------------------------------------------------------------
# Palette: sequential blue for magnitude use, two-color categorical for the
# same-continent/intercontinental split (fixed, not cycled).
# ---------------------------------------------------------------------------
COLOR_MERRA2 = '#4C72B0'
COLOR_ERA5 = '#C44E52'
CAT_COLORS = {'Same continent': '#4C72B0', 'Intercontinental': '#DD8452'}

print(f'{len(df)} flights with resolvable airport coordinates')
print(df['route_type'].value_counts())

# ---------------------------------------------------------------------------
# Panel 1: does MERRA2 agree with ERA5 flight-by-flight? (both deltas vs the
# no-wind baseline, plotted against each other -- the histograms already
# made show the marginal distributions, this shows the joint relationship.)
# ---------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(7, 7))
lims = [
    min(df['duration_delta_era5_min'].min(), df['duration_delta_merra2_min'].min()) - 5,
    max(df['duration_delta_era5_min'].max(), df['duration_delta_merra2_min'].max()) + 5,
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
    df['duration_delta_era5_min'],
    df['duration_delta_merra2_min'],
    s=14,
    color=COLOR_MERRA2,
    alpha=0.55,
    edgecolor='none',
    zorder=2,
)
r = np.corrcoef(df['duration_delta_era5_min'], df['duration_delta_merra2_min'])[0, 1]
ax.text(
    0.03,
    0.97,
    f'r = {r:.4f}\nn = {len(df)}',
    transform=ax.transAxes,
    va='top',
    fontsize=11,
    bbox=dict(boxstyle='round', facecolor='white', edgecolor='#cccccc'),
)
ax.set_xlim(lims)
ax.set_ylim(lims)
ax.set_xlabel('ERA5 wind-on duration delta vs no-wind [min]')
ax.set_ylabel('MERRA2 wind-on duration delta vs no-wind [min]')
ax.set_title('MERRA2 vs ERA5: per-flight agreement on wind-driven duration change')
ax.legend(loc='lower right', fontsize=9)
ax.spines[['top', 'right']].set_visible(False)
fig.tight_layout()
fig.savefig(
    OUTPUT_DIR / 'merra2_era5_agreement_scatter.png', dpi=150, bbox_inches='tight'
)
plt.close(fig)
print('Saved merra2_era5_agreement_scatter.png')

# ---------------------------------------------------------------------------
# Panel 2: does the MERRA2-vs-ERA5 discrepancy grow with route length, or
# with how large the wind effect itself is?
# ---------------------------------------------------------------------------
df['abs_discrepancy_min'] = df['duration_delta_merra2_vs_era5_min'].abs()
df['abs_era5_effect_min'] = df['duration_delta_era5_min'].abs()

fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))

for route_type, color in CAT_COLORS.items():
    sub = df[df['route_type'] == route_type]
    axes[0].scatter(
        sub['great_circle_km'],
        sub['duration_delta_merra2_vs_era5_min'],
        s=16,
        color=color,
        alpha=0.6,
        edgecolor='none',
        label=route_type,
    )
axes[0].axhline(0, color='black', linewidth=0.8)
axes[0].set_xlabel('Great-circle route distance [km]')
axes[0].set_ylabel('MERRA2 - ERA5 duration delta [min]')
axes[0].set_title('Discrepancy vs. route length')
axes[0].legend(fontsize=9, loc='upper right')
axes[0].spines[['top', 'right']].set_visible(False)

for route_type, color in CAT_COLORS.items():
    sub = df[df['route_type'] == route_type]
    axes[1].scatter(
        sub['abs_era5_effect_min'],
        sub['abs_discrepancy_min'],
        s=16,
        color=color,
        alpha=0.6,
        edgecolor='none',
        label=route_type,
    )
axes[1].set_xlabel('|ERA5 wind effect| (vs no-wind) [min]')
axes[1].set_ylabel('|MERRA2 - ERA5| discrepancy [min]')
axes[1].set_title('Discrepancy vs. size of the wind effect itself')
axes[1].legend(fontsize=9, loc='upper right')
axes[1].spines[['top', 'right']].set_visible(False)

fig.tight_layout()
fig.savefig(
    OUTPUT_DIR / 'merra2_era5_discrepancy_drivers.png', dpi=150, bbox_inches='tight'
)
plt.close(fig)
print('Saved merra2_era5_discrepancy_drivers.png')

# ---------------------------------------------------------------------------
# Panel 3: refined histogram, split by route type, with summary stats.
# ---------------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(9, 5.5))
bins = np.linspace(
    df['duration_delta_merra2_vs_era5_min'].min(),
    df['duration_delta_merra2_vs_era5_min'].max(),
    45,
)
for route_type, color in CAT_COLORS.items():
    sub = df[df['route_type'] == route_type]
    ax.hist(
        sub['duration_delta_merra2_vs_era5_min'],
        bins=bins,
        color=color,
        alpha=0.7,
        label=f'{route_type} (n={len(sub)})',
    )
ax.axvline(0, color='black', linewidth=0.8)
median = df['duration_delta_merra2_vs_era5_min'].median()
ax.axvline(
    median,
    color='#333333',
    linewidth=1.2,
    linestyle=':',
    label=f'Median = {median:+.3f} min',
)
ax.set_xlabel('MERRA2 - ERA5 duration delta [min]')
ax.set_ylabel('Number of flights')
ax.set_title('MERRA2 vs ERA5 per-flight duration discrepancy, by route type')
ax.legend(fontsize=9)
ax.spines[['top', 'right']].set_visible(False)
fig.tight_layout()
fig.savefig(
    OUTPUT_DIR / 'merra2_era5_discrepancy_histogram_by_route.png',
    dpi=150,
    bbox_inches='tight',
)
plt.close(fig)
print('Saved merra2_era5_discrepancy_histogram_by_route.png')

# ---------------------------------------------------------------------------
# Panel 4: geographic map of routes, colored by |MERRA2 - ERA5| discrepancy.
# ---------------------------------------------------------------------------
import cartopy.crs as ccrs  # noqa: E402
import cartopy.feature as cfeature  # noqa: E402
from matplotlib.cm import ScalarMappable  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402

fig, ax = plt.subplots(figsize=(15, 8), subplot_kw={'projection': ccrs.Robinson()})
ax.add_feature(cfeature.LAND, facecolor='#f0f0f0')
ax.add_feature(cfeature.COASTLINE, linewidth=0.4, edgecolor='#999999')
ax.set_global()

vmax = df['abs_discrepancy_min'].quantile(0.95)
norm = Normalize(vmin=0, vmax=vmax)
cmap = plt.colormaps['viridis']

df_sorted = df.sort_values('abs_discrepancy_min')
for _, row in df_sorted.iterrows():
    ax.plot(
        [row['origin_longitude_deg'], row['dest_longitude_deg']],
        [row['origin_latitude_deg'], row['dest_latitude_deg']],
        transform=ccrs.Geodetic(),
        color=cmap(norm(row['abs_discrepancy_min'])),
        linewidth=1.1,
        alpha=0.75,
    )

sm = ScalarMappable(norm=norm, cmap=cmap)
cbar = fig.colorbar(sm, ax=ax, shrink=0.6, pad=0.02)
cbar.set_label('|MERRA2 - ERA5| duration discrepancy [min] (clipped at 95th pct)')
ax.set_title(
    f'Where MERRA2 and ERA5 disagree most (2022-11-21, {len(df)} sampled routes)'
)

fig.savefig(
    OUTPUT_DIR / 'merra2_era5_discrepancy_map.png', dpi=150, bbox_inches='tight'
)
plt.close(fig)
print('Saved merra2_era5_discrepancy_map.png')

# ---------------------------------------------------------------------------
# Console summary.
# ---------------------------------------------------------------------------
print('\nSummary by route type:')
print(
    df.groupby('route_type')[
        ['duration_delta_merra2_vs_era5_min', 'abs_discrepancy_min', 'great_circle_km']
    ]
    .agg(['mean', 'median', 'std'])
    .to_string()
)
dist_corr = df['abs_discrepancy_min'].corr(df['great_circle_km'])
print(f'\nCorrelation(|discrepancy|, distance): {dist_corr:.3f}')
era5_corr = df['abs_discrepancy_min'].corr(df['abs_era5_effect_min'])
print(f'Correlation(|discrepancy|, |ERA5 effect|): {era5_corr:.3f}')
