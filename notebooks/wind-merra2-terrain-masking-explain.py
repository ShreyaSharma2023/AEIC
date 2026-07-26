# Detailed explanation of why 70/935 MERRA2 flights failed with "ground
# track point is outside weather data domain".
#
# Root cause (verified empirically, see wind_debug_merra2_failure.py):
# NOT the originally-guessed "1000 hPa cap + sea-level pressure" mechanism.
# MERRA2's inst3_3d_asm_Np product reports wind on FIXED PRESSURE SURFACES
# (e.g. 900 hPa), but a fixed pressure surface is not a fixed altitude -- at
# high terrain, the altitude corresponding to that pressure can be BELOW
# the ground. NASA masks those grid cells as NaN ("this pressure level is
# literally underground here"), which is standard practice for pressure-level
# reanalysis products near mountainous terrain. AEIC's Weather class converts
# the aircraft's altitude to a pressure via the ISA standard atmosphere and
# interpolates the wind field at that pressure -- if the flight climbs/
# descends over high terrain, that interpolation point can land inside the
# masked (NaN) region even though the pressure value itself (e.g. 900 hPa)
# is nowhere near MERRA2's actual level range (1000-0.1 hPa).
#
# Confirmed against real failures: instrumenting individual flights showed
# every one failing near a major mountain range (Andes: CIX->LIM, CUE->UIO;
# Himalayas: DEL->IXB, KTM->PBH; Tibetan Plateau: LHW->DNH, GYS->LXA;
# Alps: NCE->CPH, ZRH->FCO, AMS->ZRH; Atlas: RAK->BHX; Tian Shan: URC routes;
# Pamirs: DYU->ZIA; Caucasus: EVN->AER) -- not sea-level airports.

from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

OUTPUT_DIR = Path('/net/d16/data/shreya22/Paper_1/wind_phase1_output')
MERRA2_FILE = (
    '/net/d16/data/shreya22/Paper_1/weather_test_merra2/'
    'MERRA2_400.inst3_3d_asm_Np.20221121.nc4'
)

NCE = (7.2138, 43.6584)  # (lon, lat)
CPH = (12.6561, 55.6180)

# ---------------------------------------------------------------------------
# Figure 1: real MERRA2 900 hPa wind field over the Alps, showing the NaN
# terrain mask directly, with the NCE->CPH great-circle path overlaid.
# ---------------------------------------------------------------------------
ds = xr.open_dataset(MERRA2_FILE)
field = ds.sel(time='2022-11-21T10:00:00', method='nearest').sel(
    lev=900.0, method='nearest'
)
field = field.sel(lat=slice(38, 50), lon=slice(0, 18))

speed = (field['U'] ** 2 + field['V'] ** 2) ** 0.5
is_masked = field['U'].isnull()

fig, ax = plt.subplots(figsize=(11, 6.5), subplot_kw={'projection': ccrs.PlateCarree()})
ax.add_feature(cfeature.COASTLINE, linewidth=0.5, color='#444444')
ax.add_feature(cfeature.BORDERS, linewidth=0.4, linestyle=':', color='#666666')
ax.set_extent([0, 18, 38, 50], crs=ccrs.PlateCarree())

mesh = ax.pcolormesh(
    field['lon'],
    field['lat'],
    speed,
    cmap='viridis',
    shading='auto',
    transform=ccrs.PlateCarree(),
    alpha=0.9,
)
fig.colorbar(mesh, ax=ax, label='Wind speed at 900 hPa [m/s]', shrink=0.7)

# Overlay the NaN ("underground") mask distinctly.
masked_overlay = np.where(is_masked.values, 1.0, np.nan)
ax.pcolormesh(
    field['lon'],
    field['lat'],
    masked_overlay,
    cmap='Reds',
    vmin=0,
    vmax=1,
    shading='auto',
    transform=ccrs.PlateCarree(),
    alpha=0.55,
)

ax.plot(
    [NCE[0], CPH[0]],
    [NCE[1], CPH[1]],
    transform=ccrs.Geodetic(),
    color='black',
    linewidth=2,
    linestyle='-',
    marker='o',
    markersize=5,
    markevery=[0],
    label='NCE -> CPH great-circle path (departs NCE, heads N off-frame)',
)
ax.text(NCE[0] + 0.4, NCE[1] - 0.6, 'NCE', ha='left', fontsize=10, fontweight='bold')

ax.set_title(
    '900 hPa is "underground" over the Alps: red = MERRA2 terrain-masked\n'
    '(pressure surface below ground), not a real wind field gap'
)
ax.legend(loc='lower right', fontsize=9)

fig.savefig(
    OUTPUT_DIR / 'merra2_terrain_mask_alps_case.png', dpi=150, bbox_inches='tight'
)
plt.close(fig)
print('Saved merra2_terrain_mask_alps_case.png')

# ---------------------------------------------------------------------------
# Figure 2: conceptual cross-section -- terrain profile vs. a constant-
# pressure surface, showing where the surface dips below ground.
# ---------------------------------------------------------------------------
x = np.linspace(0, 400, 400)  # km along a cross-section
# Stylized terrain profile: flat, then a mountain range, then flat again
# (loosely evocative of an Alps crossing, not real elevation data).
terrain_m = (
    300
    + 2600 * np.exp(-((x - 180) ** 2) / (2 * 55**2))
    + 800 * np.exp(-((x - 130) ** 2) / (2 * 20**2))
    + 600 * np.exp(-((x - 230) ** 2) / (2 * 25**2))
)

# Altitude corresponding to the 900 hPa pressure surface (ISA), roughly constant.
from AEIC.utils.standard_atmosphere import pressure_at_altitude_isa_bada4  # noqa: E402

altitudes_m = np.linspace(0, 4000, 400)
pressures_hpa = pressure_at_altitude_isa_bada4(altitudes_m) / 100.0
alt_at_900hpa = np.interp(900.0, pressures_hpa[::-1], altitudes_m[::-1])

fig, ax = plt.subplots(figsize=(11, 5.5))
ax.fill_between(
    x, 0, terrain_m, color='#8B7355', alpha=0.6, label='Terrain (Alps, stylized)'
)
ax.axhline(
    alt_at_900hpa,
    color='#4C72B0',
    linewidth=2,
    label='900 hPa pressure surface (ISA altitude)',
)

below = terrain_m > alt_at_900hpa
ax.fill_between(
    x,
    alt_at_900hpa,
    terrain_m,
    where=below,
    color='#C44E52',
    alpha=0.4,
    label='900 hPa is underground here -> NaN in MERRA2',
)

ax.set_xlabel('Distance along cross-section [km]')
ax.set_ylabel('Altitude [m]')
ax.set_title(
    'Why a fixed pressure surface can be "underground": terrain vs. 900 hPa altitude'
)
ax.legend(loc='upper right', fontsize=9.5)
ax.set_ylim(0, 4200)
ax.spines[['top', 'right']].set_visible(False)

fig.tight_layout()
fig.savefig(
    OUTPUT_DIR / 'merra2_terrain_mask_schematic.png', dpi=150, bbox_inches='tight'
)
plt.close(fig)
print('Saved merra2_terrain_mask_schematic.png')

# ---------------------------------------------------------------------------
# Figure 3: world map of ALL 70 domain failures, to show this is a general
# mountain-range pattern, not a one-off.
# ---------------------------------------------------------------------------
import pandas as pd  # noqa: E402

merra2_results = pd.read_csv(OUTPUT_DIR / 'dayrun_merra2.csv')
manifest = pd.read_csv(OUTPUT_DIR / 'dayrun_manifest.csv')
airports = pd.read_csv('/home/shreya22/AEIC_3.0/src/AEIC/data/airports/airports.csv')
airports = (
    airports[['iata_code', 'latitude_deg', 'longitude_deg']]
    .dropna()
    .drop_duplicates('iata_code')
    .set_index('iata_code')
)

failed = merra2_results[~merra2_results['success']]
failed_ve = failed[
    failed['error'].str.contains('outside weather data domain', na=False)
]
routes = manifest[manifest['flight_id'].isin(failed_ve['flight_id'])][
    ['origin', 'destination']
]
routes = routes.join(airports.add_prefix('origin_'), on='origin').join(
    airports.add_prefix('dest_'), on='destination'
)
routes = routes.dropna()

fig, ax = plt.subplots(figsize=(15, 8), subplot_kw={'projection': ccrs.Robinson()})
ax.add_feature(cfeature.LAND, facecolor='#f0f0f0')
ax.add_feature(cfeature.COASTLINE, linewidth=0.4, edgecolor='#999999')
ax.set_global()

for _, row in routes.iterrows():
    ax.plot(
        [row['origin_longitude_deg'], row['dest_longitude_deg']],
        [row['origin_latitude_deg'], row['dest_latitude_deg']],
        transform=ccrs.Geodetic(),
        color='#C44E52',
        linewidth=1.3,
        alpha=0.8,
    )

# Rough major-mountain-range markers for visual reference.
ranges = {
    'Andes': (-70, -20),
    'Himalaya/Tibet': (85, 30),
    'Alps': (10, 46),
    'Atlas': (-6, 31),
    'Tian Shan': (78, 42),
    'Caucasus': (44, 42.5),
    'Pamir': (72, 38.5),
}
for name, (lon, lat) in ranges.items():
    ax.text(
        lon,
        lat,
        name,
        transform=ccrs.PlateCarree(),
        fontsize=8.5,
        ha='center',
        color='#333333',
        fontweight='bold',
        bbox=dict(
            boxstyle='round', facecolor='white', alpha=0.7, edgecolor='none', pad=0.2
        ),
    )

ax.set_title(
    f'All {len(routes)} MERRA2 "outside weather domain" failures -- '
    'clustered near major mountain ranges'
)
fig.savefig(
    OUTPUT_DIR / 'merra2_terrain_mask_all_failures_map.png',
    dpi=150,
    bbox_inches='tight',
)
plt.close(fig)
print('Saved merra2_terrain_mask_all_failures_map.png')
