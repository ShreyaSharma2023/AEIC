# Phase 1 MERRA2-vs-ERA5 resolution test: visual sanity-check of the MERRA2
# wind field, for direct comparison against wind-phase1-inspect.py's ERA5 plot.
#
# Data: /net/d16/data/shreya22/Paper_1/weather_test_merra2/
#       MERRA2_400.inst3_3d_asm_Np.20221121.nc4
# (transferred by the user from another cluster -- NASA's pressure-level MERRA2
# product, "Np", pre-interpolated onto fixed pressure levels like ERA5, unlike
# the model-level A3dyn product also in this dir which is NOT used here.
# Native 0.5x0.625 deg, instantaneous, 3-hourly at exact marks 00,03,...21 UTC.)

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import xarray as xr

FILE = (
    '/net/d16/data/shreya22/Paper_1/weather_test_merra2/'
    'MERRA2_400.inst3_3d_asm_Np.20221121.nc4'
)

# Same level/time/domain as wind-phase1-inspect.py's ERA5 plot, for a direct
# side-by-side comparison.
PRESSURE_LEVEL_HPA = 250.0
HOUR_UTC = 12

LON_MIN, LON_MAX = -125.0, -65.0
LAT_MIN, LAT_MAX = 25.0, 55.0

ds = xr.open_dataset(FILE)

target_hours = ds['time'].dt.floor('h').values
target = [t for t in target_hours if t.astype('datetime64[h]').item().hour == HOUR_UTC][
    0
]
field = ds.sel(lev=PRESSURE_LEVEL_HPA, time=target, method='nearest')

field = field.sel(lat=slice(LAT_MIN, LAT_MAX), lon=slice(LON_MIN, LON_MAX))

u = field['U']
v = field['V']
speed = (u**2 + v**2) ** 0.5

print(f'File: {FILE}')
print(f'Pressure level: {float(field["lev"]):.0f} hPa')
print(f'Valid time: {field["time"].values}')
speed_min, speed_max = float(speed.min()), float(speed.max())
print(f'Wind speed range in domain: {speed_min:.1f} - {speed_max:.1f} m/s')
print(f'Mean wind speed: {float(speed.mean()):.1f} m/s')

fig, ax = plt.subplots(figsize=(10, 7), subplot_kw={'projection': ccrs.PlateCarree()})
ax.add_feature(cfeature.COASTLINE)
ax.add_feature(cfeature.BORDERS, linestyle=':')
ax.set_extent([LON_MIN, LON_MAX, LAT_MIN, LAT_MAX], crs=ccrs.PlateCarree())

# MERRA2 is already coarser (0.5 deg) than ERA5 (0.25 deg) -- lighter subsample.
step = 2
mesh = ax.pcolormesh(
    field['lon'],
    field['lat'],
    speed,
    cmap='viridis',
    shading='auto',
    transform=ccrs.PlateCarree(),
    alpha=0.85,
)
ax.quiver(
    field['lon'][::step],
    field['lat'][::step],
    u[::step, ::step],
    v[::step, ::step],
    transform=ccrs.PlateCarree(),
    scale=1200,
    width=0.002,
)
fig.colorbar(mesh, ax=ax, label='Wind speed [m/s]', shrink=0.8)

ax.plot(
    -73.7789,
    40.6413,
    marker='*',
    color='red',
    markersize=14,
    transform=ccrs.PlateCarree(),
)
ax.text(-73.7789, 40.6413 + 1, 'JFK', color='red', transform=ccrs.PlateCarree())
ax.plot(
    -118.4085,
    33.9425,
    marker='*',
    color='red',
    markersize=14,
    transform=ccrs.PlateCarree(),
)
ax.text(-118.4085, 33.9425 + 1, 'LAX', color='red', transform=ccrs.PlateCarree())

ax.set_title(f'MERRA2 wind at {float(field["lev"]):.0f} hPa, {field["time"].values}')

out_path = (
    '/net/d16/data/shreya22/Paper_1/wind_phase1_output/wind_phase1_merra2_inspect.png'
)
fig.savefig(out_path, dpi=150, bbox_inches='tight')
print(f'Saved plot to {out_path}')
