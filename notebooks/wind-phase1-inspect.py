# Phase 1 wind testing: visual sanity-check of the wind field before
# trusting any downstream trajectory comparisons.
#
# Data: /net/d16/data/travnik/era5/wind_and_temperature/ERA5_DD_MM_YYYY.nc
# (Marek T's raw ERA5 source for his wind-optimal-trajectory work -- traced
# via his src/weather/directories.py WEATHER_DATA_PATH. Global, hourly,
# full pressure profile 100-1000 hPa, Nov 2022 - Nov 2023. Read directly
# from his directory, no local copy -- coords here are native (level, time),
# only AEIC.weather.Weather (used in wind-phase1-compare.py /
# wind-phase1-variables.py) needs the pressure_level/valid_time rename.

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import xarray as xr

WEATHER_DIR = '/net/d16/data/travnik/era5/wind_and_temperature'
FILE = f'{WEATHER_DIR}/ERA5_21_11_2022.nc'

# 250 hPa is a typical cruise-altitude pressure level (~34,000 ft) where the
# jet stream is strongest -- good for a clear tailwind/headwind sanity check.
PRESSURE_LEVEL_HPA = 250.0
HOUR_UTC = 12

# Bounding box covering JFK <-> LAX.
LON_MIN, LON_MAX = -125.0, -65.0
LAT_MIN, LAT_MAX = 25.0, 55.0

ds = xr.open_dataset(FILE)

target_hours = ds['time'].dt.floor('h').values
target = [t for t in target_hours if t.astype('datetime64[h]').item().hour == HOUR_UTC][
    0
]
field = ds.sel(level=PRESSURE_LEVEL_HPA, time=target, method='nearest')

field = field.sel(latitude=slice(LAT_MAX, LAT_MIN), longitude=slice(LON_MIN, LON_MAX))

u = field['u']
v = field['v']
speed = (u**2 + v**2) ** 0.5

print(f'File: {FILE}')
print(f'Pressure level: {float(field["level"]):.0f} hPa')
print(f'Valid time: {field["time"].values}')
speed_min, speed_max = float(speed.min()), float(speed.max())
print(f'Wind speed range in domain: {speed_min:.1f} - {speed_max:.1f} m/s')
print(f'Mean wind speed: {float(speed.mean()):.1f} m/s')

fig, ax = plt.subplots(figsize=(10, 7), subplot_kw={'projection': ccrs.PlateCarree()})
ax.add_feature(cfeature.COASTLINE)
ax.add_feature(cfeature.BORDERS, linestyle=':')
ax.set_extent([LON_MIN, LON_MAX, LAT_MIN, LAT_MAX], crs=ccrs.PlateCarree())

# Subsample for a readable quiver plot (0.25 deg native res is dense).
step = 4
mesh = ax.pcolormesh(
    field['longitude'],
    field['latitude'],
    speed,
    cmap='viridis',
    shading='auto',
    transform=ccrs.PlateCarree(),
    alpha=0.85,
)
ax.quiver(
    field['longitude'][::step],
    field['latitude'][::step],
    u[::step, ::step],
    v[::step, ::step],
    transform=ccrs.PlateCarree(),
    scale=1200,
    width=0.002,
)
fig.colorbar(mesh, ax=ax, label='Wind speed [m/s]', shrink=0.8)

# Mark JFK and LAX for reference.
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

ax.set_title(f'ERA5 wind at {float(field["level"]):.0f} hPa, {field["time"].values}')

out_path = '/net/d16/data/shreya22/Paper_1/wind_phase1_output/wind_phase1_inspect.png'
fig.savefig(out_path, dpi=150, bbox_inches='tight')
print(f'Saved plot to {out_path}')
