# Pressure levels vs. model (hybrid-sigma) levels: why one is terrain-
# following and the other isn't, and why that's exactly what caused the
# MERRA2 "outside weather data domain" failures.

import matplotlib.pyplot as plt
import numpy as np

OUT_PATH = (
    '/net/d16/data/shreya22/Paper_1/wind_phase1_output/'
    'pressure_vs_model_levels_explained.png'
)

x = np.linspace(0, 400, 400)
terrain_m = (
    300
    + 2600 * np.exp(-((x - 180) ** 2) / (2 * 55**2))
    + 800 * np.exp(-((x - 130) ** 2) / (2 * 20**2))
    + 600 * np.exp(-((x - 230) ** 2) / (2 * 25**2))
)

fig, axes = plt.subplots(1, 2, figsize=(14, 5.5), sharey=True)

# --- Panel A: pressure levels (isobaric surfaces) ---
ax = axes[0]
ax.fill_between(x, 0, terrain_m, color='#8B7355', alpha=0.6, zorder=2)
pressure_alts = [700, 1200, 1800, 2400, 3000, 3800]
for i, alt in enumerate(pressure_alts):
    below = terrain_m > alt
    color = '#4C72B0'
    ax.axhline(alt, color=color, linewidth=1.5, alpha=0.9, zorder=3)
    if below.any():
        y = np.full_like(x, alt, dtype=float)
        y[~below] = np.nan
        ax.plot(x, y, color='#C44E52', linewidth=4, zorder=4)
ax.set_title(
    'Pressure levels (e.g. MERRA2 "Np")\nFixed, flat surfaces -- NOT terrain-following'
)
ax.text(
    20,
    3950,
    'Blue = valid; thick red = "underground"\n'
    '(masked NaN by MERRA2; ERA5 extrapolates instead)',
    fontsize=8.5,
    va='top',
    color='#333333',
)

# --- Panel B: model / hybrid-sigma levels ---
ax2 = axes[1]
ax2.fill_between(x, 0, terrain_m, color='#8B7355', alpha=0.6, zorder=2)
n_levels = 6
for i in range(1, n_levels + 1):
    frac = i / n_levels
    # Terrain-following near the surface, flattening out with height
    # (illustrative hybrid-sigma shape, not real coefficients).
    level_y = terrain_m * (1 - frac) ** 0.5 * 0.15 + frac * 4000 * (
        1 - 0.25 * np.exp(-frac * 3)
    )
    ax2.plot(x, level_y, color='#55A868', linewidth=1.5, alpha=0.9, zorder=3)
ax2.set_title(
    'Model / hybrid-sigma levels (e.g. GEOS-Chem "A3dyn")\n'
    'Terrain-following -- always above the local ground'
)
ax2.text(
    20,
    3950,
    'Green = model levels\nNever "underground" by construction,\n'
    'but level N means a different real\naltitude/pressure at every location',
    fontsize=8.5,
    va='top',
    color='#333333',
)

for a in axes:
    a.set_xlabel('Distance along cross-section [km]')
    a.set_ylim(0, 4200)
    a.spines[['top', 'right']].set_visible(False)
axes[0].set_ylabel('Altitude [m]')

fig.suptitle(
    'Why MERRA2\'s pressure-level product fails over terrain, and model levels don\'t',
    fontsize=13,
)
fig.tight_layout()
fig.savefig(OUT_PATH, dpi=150, bbox_inches='tight')
print(f'Saved {OUT_PATH}')
