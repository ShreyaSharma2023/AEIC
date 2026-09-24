# PIANO performance model

The {py:class}`PianoPerformanceModel
<AEIC.performance.models.PianoPerformanceModel>` class represents a
performance model built from PIANO climb, cruise and descent exports. Files
for this model type use `model_type = "piano"`.

The source data is read by {py:mod}`AEIC.parsers.piano_reader`, which is
documented in [PIANO reader](../parsers/piano_reader.md).

## Differences from the legacy model

 * Cruise is swept over Mach number as well as flight level and mass, whereas
   the legacy model is only swept over flight level and mass.
 * Each phase table carries thrust, drag and trajectory columns in addition to
   fuel flow, mass, flight level, TAS, and rate of climb/descent.
 * There is no fixed cruise speed in the exports because Mach number is
   swept. {py:attr}`speeds <AEIC.performance.models.BasePerformanceModel.speeds>`
   has a `climb` and a `descent` entry read from the exports, and a `cruise`
   entry only if the person building the model supplies one (see
   [Cruise speed schedule](#cruise-speed-schedule)).
 * Two extra tables come along with the phase tables:
   `cruise_reference_mach` and `descent_idle_thrust`.

## Performance evaluation

Climb and descent are evaluated by bilinear interpolation in flight level and
aircraft mass, as for the legacy model. Values outside the table are clipped to
its edge rather than extrapolated. The mass of a climb or descent table row is
the starting mass of its block.

A model is checked when it is loaded: each phase table must have a row at every
(flight level, mass) pair, and the cruise table must have a Mach sweep at every
pair. A table with a gap is rejected, naming the aircraft and the phase, rather
than being interpolated across the gap.

Cruise is evaluated at the Mach number given by the cruise speed schedule (next
section), interpolating linearly in Mach between the tabulated values in each
(flight level, mass) cell. PIANO leaves out the speeds an aircraft cannot
sustain at a given flight level and mass, so the Mach range can differ from cell
to cell. Asking for a Mach outside the range of a cell the query needs is an
error, since the nearest tabulated Mach is a different speed.

## Cruise speed schedule

Evaluating cruise performance needs `speeds.cruise`, which the exports do not
provide. It holds `cas_low`, `cas_high` and `mach` (the design Mach), with CAS
in m/s, and is supplied through the `cruise_speeds` argument of
{py:func}`build_piano_model <AEIC.performance.model_builder.build_piano_model>`.
Evaluating cruise without it is an error naming the aircraft.

The aircraft flies a constant CAS, `cas_low` below FL100 and `cas_high` above,
until the Mach number of that CAS reaches the design Mach, and the design Mach
above that. This is the lower of the design Mach and the Mach number of the
CAS at the aircraft's altitude, in a standard atmosphere.

## Operating empty mass

`operating_empty_mass_kg` is a required field for a PIANO performance model.
PIANO exports contain no operating empty mass, and we cannot apply the BADA 3
rule to estimate it. This field must be passed when creating the
performance model file.

## Performance tables

The three phase tables are {py:class}`PerformanceTableInput
<AEIC.performance.models.base.PerformanceTableInput>` values, so each must
provide the `fuel_flow`, `fl`, `tas`, `rocd` and `mass` columns.

Row ordering in a generated file is by `(mass, fl)` for climb and descent, and
by `(mass, fl, mach)` for cruise.

### Climb table

| Column | Unit | Meaning |
|--------|------|---------|
| `fl` | flight level | Altitude. |
| `mass` | kg | Aircraft mass, the block's initial mass. |
| `tas` | m/s | True airspeed, derived from the airspeed schedule. |
| `rocd` | m/s | Rate of climb. |
| `fuel_flow` | kg/s | Fuel flow, derived from the burn and time deltas. |
| `time` | s | Cumulative from the start of the phase. |
| `distance` | m | Cumulative from the start of the phase. |
| `burn` | kg | Cumulative from the start of the phase. |
| `fn_per_engine` | N | Net thrust per engine. |
| `drag` | N | Drag. |

### Cruise table

| Column | Unit | Meaning |
|--------|------|---------|
| `fl` | flight level | Altitude. |
| `mass` | kg | Aircraft mass. |
| `mach` | - | Mach number of this point of the sweep. |
| `tas` | m/s | True airspeed. |
| `cas` | m/s | Calibrated airspeed. |
| `rocd` | m/s | Always zero: cruise is level flight. |
| `fuel_flow` | kg/s | Fuel flow. |
| `drag` | N | Drag. |
| `mcr_pct` | percent | Percent of maximum cruise thrust. |
| `lift_to_drag` | - | Lift-to-drag ratio. |
| `sfc` | kg/(N.s) | Specific fuel consumption. |
| `sar` | m/kg | Specific air range. |
| `mcl_avail_per_engine` | N | Maximum climb thrust available per engine. |
| `rocd_mcl_fix_mach` | m/s | Rate of climb at maximum climb thrust, fixed Mach. |
| `rocd_mcl_fix_cas` | m/s | Rate of climb at maximum climb thrust, fixed CAS. |

The two `rocd_mcl_*` columns keep the sign PIANO reports, such that they can be
negative where the aircraft cannot keep steady level flight even at the max climb
rating setting.

Due to rounding in the PIANO output, a `(fl, mass, Mach)` triplet might be
the same for a row coming from the PIANO Mach sweep, and a row coming from
one of the operating points but with slightly differing performance values.
In this case, the operating point value is kept over the Mach sweep.

### Descent table

| Column | Unit | Meaning |
|--------|------|---------|
| `fl` | flight level | Altitude. |
| `mass` | kg | Aircraft mass. |
| `tas` | m/s | True airspeed, derived from the airspeed schedule. |
| `rocd` | m/s | Rate of descent, negative. |
| `fuel_flow` | kg/s | Fuel flow, derived from the burn and time deltas. |
| `time` | s | Cumulative from the start of the phase. |
| `distance` | m | Cumulative from the start of the phase. |
| `burn` | kg | Cumulative from the start of the phase. |
| `fn_per_engine` | N | Net thrust per engine. |

### Cruise reference Mach table

| Column | Unit | Meaning |
|--------|------|---------|
| `fl` | flight level | Altitude. |
| `mass` | kg | Aircraft mass. |
| `max_sar` | - | Mach at maximum specific air range. |
| `sar_99` | - | Mach at 99% of maximum specific air range. |
| `max_lim` | - | Maximum limiting Mach. |

Every (flight level, mass) group is written. If the PIANO file does not have
one of the three reference Mach numbers for a group, that column is written as
`nan`.

### Descent idle thrust table

| Column | Unit | Meaning |
|--------|------|---------|
| `mass` | kg | Aircraft mass. |
| `idle_thrust_altitude` | m | Altitude below which idle thrust is used. |

This table can be empty, meaning that no descent block gave an idle
thrust altitude.

## Input file format

The following shows the structure of a PIANO performance model file. Field
order comes from `PIANO_WRITE_SPEC` in
{py:mod}`AEIC.performance.model_builder`, which is what
{command}`make-performance-model` writes.

```
# Performance model type (one of: legacy, bada, tasopt, piano).
model_type = "piano"

# ==============================================================================
#
#  COMMON FIELDS
#
# Fields common to all performance model types.

aircraft_name = "..."
aircraft_class = "narrow" # wide, narrow, small, freight
ISA_offset = 0
maximum_altitude_ft = 41000
maximum_payload_kg = 22422
number_of_engines = 2 # Number of engines
operating_empty_mass_kg = 41413.0 # kg
APU_name = "APU 131-9" # None: APU emissions not calculated

# ------------------------------------------------------------------------------
#
# Speed data
#
# There is no [speeds.cruise] table: PIANO sweeps cruise Mach number, so a
# single cruise speed is meaningless.

[speeds.climb]
cas_low = 0.0
cas_high = 0.0
mach = 0.0
crossover_altitude_m = 0.0

[speeds.descent]
cas_low = 0.0
cas_high = 0.0
mach = 0.0
crossover_altitude_m = 0.0

# ------------------------------------------------------------------------------
#
# LTO data
#

[LTO_performance]
source = "EDB"
ICAO_UID = "01P11CM121" # Add UID for EDB data
rated_thrust = 0.0

[LTO_performance.mode_data.idle]
thrust_frac = 0.0
fuel_kgs    = 0.0
EI_NOx      = 0.0
EI_HC       = 0.0
EI_CO       = 0.0

# ... approach, climb and takeoff sub-tables. ...

# ==============================================================================
#
#  MODEL-TYPE SPECIFIC FIELDS
#

# ------------------------------------------------------------------------------
#
# Performance table data.
#

[climb_flight_performance]
cols = [
  "fl",  # Flight levels
  "mass",  # kg
  "tas",  # m/s
  "rocd",  # m/s
  "fuel_flow",  # kg/s - REQUIRED; OUTPUT COLUMN
  "time",  # s - cumulative from start of phase
  "distance",  # m - cumulative from start of phase
  "burn",  # kg - cumulative from start of phase
  "fn_per_engine",  # N - net thrust per engine
  "drag"  # N
]

data = [
  ...
]

[cruise_flight_performance]
cols = [
  "fl",  # Flight levels
  "mass",  # kg
  "mach",  # Mach number
  "tas",  # m/s
  "cas",  # m/s
  "rocd",  # m/s
  "fuel_flow",  # kg/s - REQUIRED; OUTPUT COLUMN
  "drag",  # N
  "mcr_pct",  # percent of maximum cruise thrust
  "lift_to_drag",  # Lift-to-drag ratio
  "sfc",  # kg/(N.s) - specific fuel consumption
  "sar",  # m/kg - specific air range
  "mcl_avail_per_engine",  # N - maximum climb thrust available per engine
  "rocd_mcl_fix_mach",  # m/s - at maximum climb thrust, fixed Mach
  "rocd_mcl_fix_cas"  # m/s - at maximum climb thrust, fixed CAS
]

data = [
  ...
]

[descent_flight_performance]
cols = [
  "fl",  # Flight levels
  "mass",  # kg
  "tas",  # m/s
  "rocd",  # m/s
  "fuel_flow",  # kg/s - REQUIRED; OUTPUT COLUMN
  "time",  # s - cumulative from start of phase
  "distance",  # m - cumulative from start of phase
  "burn",  # kg - cumulative from start of phase
  "fn_per_engine"  # N - net thrust per engine
]

data = [
  ...
]

[cruise_reference_mach]
cols = [
  "fl",  # Flight levels
  "mass",  # kg
  "max_sar",  # Mach at maximum specific air range
  "sar_99",  # Mach at 99% of maximum specific air range
  "max_lim"  # Maximum limiting Mach
]

data = [
  ...
]

[descent_idle_thrust]
cols = [
  "mass",  # kg
  "idle_thrust_altitude"  # m - altitude below which idle thrust is used
]

data = []
```

## Performance evaluation

% TODO: fill this in when evaluate_impl is implemented:
% - which flight rules class the model accepts (currently SimpleFlightRules);
% - how the cruise Mach dimension is collapsed, and whether the reference Mach
%   table drives that choice;
% - whether the idle thrust table affects descent fuel flow.

Not yet implemented. {py:meth}`evaluate_impl
<AEIC.performance.models.PianoPerformanceModel.evaluate_impl>` raises
`NotImplementedError`.

## API reference

```{eval-rst}
.. autoclass:: AEIC.performance.models.PianoPerformanceModel
   :members:
   :exclude-members: model_config, model_type, require_operating_empty_mass
```
