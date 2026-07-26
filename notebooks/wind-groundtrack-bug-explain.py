# Explanatory figure for the GroundTrack.location() overstep bug found while
# running the 935-flight day-run comparison (see wind_dayrun_run_scenario.py
# and the memory notes). Not tied to real trajectory data -- illustrative
# schematic using representative numbers from the JFK<->LAX case.

import matplotlib.pyplot as plt
import numpy as np

OUT_PATH = (
    '/net/d16/data/shreya22/Paper_1/wind_phase1_output/'
    'groundtrack_overstep_bug_explained.png'
)

# Representative numbers (JFK<->LAX-scale flight).
TOTAL_DISTANCE_KM = 3900.0
CRUISE_ALT_M = 10668.0
DESCENT_DIST_APPROX_KM = (
    18.228347 * CRUISE_ALT_M / 1000.0
)  # legacy.py:199-200, altitude-only, wind-blind
CLIMB_DIST_KM = 150.0
PLANNED_DESCENT_START_KM = TOTAL_DISTANCE_KM - DESCENT_DIST_APPROX_KM

# Under a strong tailwind, ground speed during descent is higher than the
# static estimate assumed, so the same altitude-loss (same time) covers more
# ground distance -- the descent overshoots the destination before reaching
# the ground-level altitude target.
TAILWIND_OVERSHOOT_KM = 140.0
ACTUAL_DESCENT_END_KM = TOTAL_DISTANCE_KM + TAILWIND_OVERSHOOT_KM

fig, ax = plt.subplots(figsize=(11, 6))

# Climb.
climb_x = [0, CLIMB_DIST_KM]
climb_y = [0, CRUISE_ALT_M]
ax.plot(climb_x, climb_y, color='#4C72B0', linewidth=2.5, label='Climb')

# Cruise.
cruise_x = [CLIMB_DIST_KM, PLANNED_DESCENT_START_KM]
cruise_y = [CRUISE_ALT_M, CRUISE_ALT_M]
ax.plot(cruise_x, cruise_y, color='#4C72B0', linewidth=2.5, label='Cruise')

# Planned descent (what the static, wind-blind descent_dist_approx assumes).
planned_descent_x = [PLANNED_DESCENT_START_KM, TOTAL_DISTANCE_KM]
planned_descent_y = [CRUISE_ALT_M, 0]
ax.plot(
    planned_descent_x,
    planned_descent_y,
    color='#4C72B0',
    linewidth=2.5,
    linestyle='--',
    label='Planned descent (static estimate, no wind)',
)

# Actual descent under a strong tailwind: same altitude-loss rate over time,
# but higher ground speed -> covers more ground distance -> overshoots the
# destination before reaching altitude 0.
actual_descent_x = [PLANNED_DESCENT_START_KM, ACTUAL_DESCENT_END_KM]
actual_descent_y = [CRUISE_ALT_M, 0]
ax.plot(
    actual_descent_x,
    actual_descent_y,
    color='#C44E52',
    linewidth=2.5,
    label='Actual descent (real, wind-boosted ground speed)',
)

# Mark destination.
ax.axvline(TOTAL_DISTANCE_KM, color='black', linewidth=1, linestyle=':')
ax.annotate(
    'Destination\n(ground_track.total_distance)',
    xy=(TOTAL_DISTANCE_KM, 9600),
    xytext=(TOTAL_DISTANCE_KM - 500, 9600),
    ha='right',
    va='center',
    fontsize=10,
    arrowprops=dict(arrowstyle='->', color='black', linewidth=0.8),
)

# Mark the point where the old code crashed: the first descent SEGMENT step
# whose end distance already exceeds total_distance (illustrated partway
# along the actual-descent line, past the destination).
crash_x = TOTAL_DISTANCE_KM + TAILWIND_OVERSHOOT_KM * 0.35
crash_alt = np.interp(crash_x, actual_descent_x, actual_descent_y)
ax.scatter(
    [crash_x], [crash_alt], color='#C44E52', marker='x', s=200, linewidth=3, zorder=5
)
ax.annotate(
    'BEFORE FIX: GroundTrack.location(distance)\n'
    'called here, distance > total_distance\n'
    '-> unconditionally raised "distance outside\nground track range", even though\n'
    'allow_overstep=True (legacy.py:111) and\nstep() already handled this case.',
    xy=(crash_x, crash_alt),
    xytext=(1500, 5300),
    ha='left',
    va='center',
    fontsize=9.5,
    color='#C44E52',
    arrowprops=dict(arrowstyle='->', color='#C44E52', linewidth=1),
)

ax.annotate(
    "AFTER FIX: location() now checks\nallow_overstep and delegates to\n"
    '_overstep(), extrapolating along the\nfinal great-circle heading --\n'
    "matching step()'s existing behavior.",
    xy=(ACTUAL_DESCENT_END_KM, 0),
    xytext=(1500, 2200),
    ha='left',
    va='center',
    fontsize=9.5,
    color='#55A868',
    arrowprops=dict(arrowstyle='->', color='#55A868', linewidth=1),
)

ax.axvspan(TOTAL_DISTANCE_KM, ACTUAL_DESCENT_END_KM, color='#C44E52', alpha=0.06)
ax.text(
    (TOTAL_DISTANCE_KM + ACTUAL_DESCENT_END_KM) / 2,
    -900,
    'overshoot region\n(only reachable under wind)',
    ha='center',
    fontsize=8.5,
    color='#666666',
)

ax.set_xlabel('Ground distance from origin [km]')
ax.set_ylabel('Altitude [m]')
ax.set_title(
    'Why strong tailwinds crashed trajectory simulation during descent\n'
    '(GroundTrack.location() overstep bug, fixed in ground_track.py)'
)
ax.set_xlim(-100, 4300)
ax.set_ylim(-1200, 12500)
ax.legend(loc='upper left', fontsize=9.5, framealpha=0.95)
ax.spines[['top', 'right']].set_visible(False)

fig.tight_layout()
fig.savefig(OUT_PATH, dpi=150, bbox_inches='tight')
print(f'Saved {OUT_PATH}')
