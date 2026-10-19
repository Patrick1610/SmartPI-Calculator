# SmartPI Calculator

An unofficial Home Assistant wrapper around the existing SmartPI mathematical
core from [KipK/vtherm_smartpi](https://github.com/KipK/vtherm_smartpi).
No VTherm integration is required. This is not an original or rewritten PI algorithm.

**Experimental source candidate (`0.2.4`).** It computes a heating-demand percentage and can
optionally write explicitly selected valve endpoints. Start with **Learning disabled**.

Repository: [Patrick1610/SmartPI-Calculator](https://github.com/Patrick1610/SmartPI-Calculator).

## Boundaries

- Reads **room temperature**, **target temperature**, and **current outdoor temperature**
  from three selected numerical entities. No TRV-temperature dependence.
- Fixed HEAT mode; periodic updates every 30 seconds, logical bookkeeping cycles of 10 minutes.
- Publishes **Heating demand**, 0–100%, and fourteen diagnostic sensors.
- Publishes an unavailable sensor on invalid input; never substitutes a fictitious 0 °C.
- Saves learned model/PI state with Home Assistant storage and restores it after reload/restart.
- No climate entity, schedules, target-writing automations, thermal valve curve, or boiler/pump
  control. Only Direct valve control writes `number.set_value` to selected endpoints.
  Nothing is installed into an existing HA automatically.

The original control law, bootstrap, deadband, integral management and learning rules are
retained. Reaching the setpoint is **not** an additional command to shut the output off.
In the stable phase, feed-forward and retained integral can maintain a holding demand.
The original landing governor can also reduce the demand early. During bootstrap, the
upstream hysteresis may request full output and continue above target. Do not mistake this
for a thermostat that immediately goes to zero at equality with the default settings.

### Bootstrap hysteresis

Setup and Options expose two per-room configuration fields, not extra number entities:

- **Switch on below target:** a nonnegative distance, default **0.3 °C**.
- **Switch off above target:** a nonnegative distance, default **0.5 °C**;
  **0** switches off at the target.

Each distance must be finite and between 0 and 2 °C; both cannot be zero. At a
20.5 °C target, choosing 0.2 / 0 means ON at or below 20.3 °C and OFF at or above
20.5 °C. Between the boundaries the previous command is retained. Enter positive
distances, not signed offsets: the ON distance is subtracted, the OFF distance added.
These options only affect the upstream **Hysteresis bootstrap phase**, never the stable
PI deadband, gains, learning quality gates, or a generic cutoff at target.

Existing configurations without these fields keep exactly the old 0.3 / 0.5 defaults.
Changing these distances preserves the learned-model storage binding; the regular
safe close/reload behavior still applies when Options are saved. A narrower band can
shorten useful learning episodes; 0.2 / 0 is a trial setting, not a validated optimum.
RoomMind/MPC may stop heat independently before a SmartPI threshold is reached. Matching
a pair of numbers does not synchronize independent controllers or prove heat delivery.

## Control modes

Setup and Options contain one exclusive mode choice:

- **Learning disabled** (default): calculate and publish; no valve writes or learning.
- **Manual external control**: calculate and learn under your explicit assertion that an
  external controller applies the output 1:1. SmartPI itself writes nothing.
- **Direct valve control**: calculate and write complementary opening/closing values;
  pause learning until every selected number entity echoes the requested values.

There is no separate “Demand is applied” checkbox. Existing 0.1.0 configurations migrate
to Learning disabled unless that old checkbox was enabled, in which case they migrate to
Manual external control. Migration never selects Direct valve control or attaches valves.
With an empty model and learning disabled, the output remains the upstream bootstrap
calculation; merely observing cannot train a stable model.

The host retains the previous command and its time-weighted history. This is a simplified
application assumption, not measured power or a physical delivery acknowledgement. Numeric
echoes can themselves be optimistic or delayed by the underlying integration. We do not equate
40% valve command with 40% thermal watts. Variable supply-water temperature, hydraulic
competition, lost commands or a boiler outage can violate the simplified thermal model.
Emma's global demand is neither a room-power input nor a supply-temperature correction.

No extra `room >= target -> 0` rule, positive-delta-only learning gate or pump-overrun hold
has been invented. Cooling observations with zero command are needed for heat-loss learning.
Minimum hydraulic flow and boiler pump overrun are outside this integration's scope.
Confirm installation suitability before actuator use.

### Optional direct valve control

Only this mode opens a second setup page. It has two multi-entity fields: opening numbers
and closing numbers. Select equal-length lists in matching order. Each number must support
0–100; targets must be unique and cannot belong to another direct SmartPI instance.
Disable the TRV's native Adaptive mode and remove competing endpoint writers first.
For Sonoff-style complementary endpoints, the default maps demand `u` to opening `u`, closing `100-u`.
Both endpoints are rounded onto a common supported step. These values are limits/commands,
not measured physical positions. Other actuator semantics are not supported.

A third page, also exclusive to Direct mode, sets the **minimum usable opening per
radiator**. Each minimum is linked to its opening entity, not its position in the list.
Default **0** preserves the old mapping. Set a measured start of the usable range from
0 to less than 100%; no extra configuration entities are created.

For normalized demand `u` and minimum `m` (both in percent):

```
u = 0: opening = 0, closing = 100
u > 0: opening = m + (100 - m) * u / 100, closing = 100 - opening
```

Commands are rounded to supported steps; positive openings are never rounded below `m`.
For `m = 30`, demands 5 / 17 / 24% become openings 34 / 42 / 47% on a 1% grid.
This rescales the usable span rather than flattening all low demands to a fixed minimum.
The existing normalized-demand step still applies: a sub-step request may round to zero,
and coarse device steps limit modulation resolution. A very high minimum leaves little
usable span. The minimum is a command-space offset, not proof of flow or a linear heat curve.

The PI core, its contribution sensors, and its time-weighted learning history stay in
**normalized demand space**. Only the actuator adapter maps to per-radiator endpoints;
learning still requires matching echoes of those **mapped** commands. Changing a minimum
retains the learned model, real sample histories and PI integral **unchanged**, including
changing it back to zero. Range provenance is stored, but is not a model-erasure trigger.
Existing 0.2.2 bindings with minima are accepted when room/outside sources, mode and
actuator lists still match. This is data retention, not automatic thermal re-identification:
old heating estimates are a retained prior, not certified for the new physical range.
The core's existing sample filters remain authoritative. In particular, outlying heating
observations cannot recenter until the cooling coefficient is sufficiently converged;
repeated `skip: a_meas outlier` can therefore persist while cooling is not converged.
Changing a minimum does not bypass that gate or fabricate replacement samples.
Monitor physical response and **Last learning result** while tuning. Options saves retain normal safe-close
and reload behavior. Tune minima only with physical radiator observations; this integration
does not infer them automatically.

There is no automatic fresh-start action for range tuning. If a retained model is genuinely
unusable and you deliberately want to discard it, the existing Options mode binding provides
an escape hatch: save **Learning disabled**, let that reload finish, then restore the intended
control mode and (for Direct mode) verify its selected valve pairs before saving again.
Those mode changes create a fresh model for this entry, **discarding all its learned history**;
they are not a routine tuning step. Preserve an actual copy of that entry's stored model
before such a deliberate reset; diagnostic counters alone cannot restore samples.

Calculation runs every 30 seconds; ordinary writes and retries are limited to once per
endpoint per 60 seconds, with a five-second timeout per call. Unconfirmed commands remain
pending and retry; they do not create a permanent command-fault latch. While an ordinary
candidate is rate-limited, the published/applied command stays at the previous command;
the suppressed candidate is not committed as though it had been sent. Feedback is checked
before each learning step, allowing normal echoes that arrive after the service returns.
Uncertain cycles are excluded from learning without resetting the control loop. A new zero/stop
command bypasses the normal interval. Missing temperature inputs or an unavailable valve
pause learning and request closing of reachable endpoints. Unload, mode change and normal
HA shutdown also request closing. An unreachable device or power failure cannot be made
physically safe by software; numeric acknowledgements never prove that a valve closed.

RoomMind can remain the numeric target source. This adapter changes neither RoomMind nor
TRV climate targets/HVAC modes, ClimateSync sources, or boiler control. Check compatibility:
if your boiler-demand path stops heat while SmartPI retains a holding command, heat delivery
no longer matches the simplified model. This integration does not automatically reconcile
independent controllers. Keep an appropriate hydraulic bypass/pump-overrun arrangement.

## Output and diagnostics

The `Heating demand` sensor publishes **0–100%**. This is a normalized command,
not measured valve position, delivered heat or boiler modulation.

Its attributes expose the feedback assumption, PI gains, thermal-model coefficients,
control mode and static metadata. Values with dedicated sensors are not duplicated as
attributes: use those entities for temperatures, phase, learning status, valve status,
sample counts and the last learning result. Configuration diagnostics can also be downloaded from
Home Assistant. Source entity IDs are redacted in that download.
No extra valve-translation sensor is created: Heating demand, Unbounded PI demand and the four contribution sensors
describe the normalized calculation; the existing opening/closing **number entities** give
the per-radiator command history. Downloadable diagnostics include a redacted snapshot of
each configured minimum and its requested mapped endpoints, not measured positions.

Five enabled-by-default diagnostic sensors show **Used room temperature**, **Used target
temperature**, **Used outdoor temperature**, **Control phase** and **Learning status**.
Temperatures are the actual inputs of the latest successful calculation, stored internally
in °C and displayed in HA's temperature unit. Learning status is `observing`, `paused` or `enabled`; enabled is permission
to learn under assumed 1:1 application, not proof that a sample has been accepted. Its
attributes retain the feedback assumption, not duplicate sample counts.
In direct mode, `enabled` means
numeric feedback permits learning, not that measured thermal delivery has been confirmed.

Four additional enabled-by-default diagnostic sensors provide directly selectable history:

- **Valve control status:** the last adapter status. Remains readable during calculation
  failures, so `failed`/`unavailable` are not hidden behind a generic unavailable entity.
  `confirmed` still means matching numeric endpoints, not measured physical movement.
- **Accepted heating samples** and **Accepted cooling samples:** the current accepted
  sample-buffer counts from the core, not every sensor report, elapsed cycles, or unbounded
  lifetime totals. They can saturate at the core's history limit or fall after a model reset.
  No accumulating statistics state class is assigned.
- **Last learning result:** the last reason/result reported by the core, including rejected
  or extended windows. It is not a live diagnosis of the current blocker and may remain
  unchanged while learning is disabled. A restart can return it to `init` even if model
  samples were restored. No estimated reason is substituted.

Four calculation sensors expose the latest Stable PI evaluation in signed **percentage
points**, displayed with HA's `%` unit:

- **Base contribution:** bounded structural feed-forward plus slow learned trim (FF1 + FF2).
- **Proportional contribution:** the actual P term, including core deadband handling.
- **Integral contribution:** the actual retained I term, not the Ki gain or raw integral.
- **Predictive contribution:** runtime feed-forward minus the base. This is the effective
  predictive increment after FF clamping and deadband suppression, not the raw FF3 candidate.

Their sum explains the candidate before final command clamping, landing caps, rate limiting,
and actuator-step rounding; it need not equal Heating demand. Negative contributions and
sums outside 0–100 are legitimate. Bootstrap, calibration, a guard cut, and an uninitialized
PI evaluation have no valid decomposition: these sensors show unknown, never fictional zero.
Only the harness reads existing core results; vendor math and control settings are unchanged.
Recorder retention/exclusions apply, and measurement state class permits numeric statistics.

**Unbounded PI demand** (Dutch: **Onbegrensde PI-vraag**) publishes the signed sum of
those four terms from one cached evaluation, before final 0–100% output clamping, landing
caps, rate limiting and actuator-step rounding. It retains negative values and values
above 100%; for example `4.49 - 25.09 + 0 + 0 = -20.60%`, while Heating demand can be 0%.
This is a diagnostic candidate, not a cooling request, measured heat or a valve position.
Earlier component-level guards/limits still apply; “unbounded” does not mean every
internal safeguard is bypassed. Missing/invalid terms give unknown, never a partial sum
or zero. It is enabled by default, has measurement state class and two display decimals,
and uses the same Recorder policy as the other calculation sensors. Reading it does not
recalculate or change PI/learning/valve state. Existing histories are not backfilled.

There are fifteen sensors per instance. All except Valve control status become unavailable
when calculation fails. Existing sensor unique IDs are retained; a/b,
Kp/Ki, control mode and static metadata remain attributes, not additional entities.
Templates and dashboards using the removed duplicate attributes must use the corresponding
sensor entities instead. The downloadable diagnostics retain the complete model diagnostics.
Normal Recorder retention/exclusions apply; numerical sample counts and text results are
ordinary history, not automatically long-term statistics.
The integration is registered as a calculation service, visible under **Devices & services
→ Integrations**, rather than only under Helpers.

A fresh, untrained observer
uses the upstream bootstrap hysteresis; an available percentage alone does not prove
that a learned PI model is active or that heat is physically being delivered.

## Setup

Install through HACS as a **custom repository**: add
`https://github.com/Patrick1610/SmartPI-Calculator` with type **Integration**. Once source
candidate `0.2.4` has a published release, download it and restart Home Assistant.
Then select **Settings → Devices & services
→ Add integration → SmartPI Calculator**. This is not inclusion in HACS's default catalog;
that requires a separate upstream review. HACS manages files and updates, not room setup.

Requires Home Assistant **2026.10 or newer**. For manual installation instead, copy
`custom_components/smartpi_calculator/` into HA's `custom_components/` and restart HA.
Choose a name and the three source entities. The target source is a numeric state, not a
climate attribute; an existing RoomMind target sensor is suitable. Values with °F are converted
to °C; a missing unit uses HA's configured temperature unit (for numerical target helpers).
Unknown/unavailable/non-finite/unsupported-unit readings suspend calculation and learning.
The sources' real reporting freshness is **not inferred** from recorder history or guaranteed
by this prototype. A stale but numeric available sensor can remain an undetected bad input.

Example sources (select the corresponding entities in your own installation):

- Room: `sensor.room_temperature`
- Target: `sensor.room_heating_target` (for example a numerical RoomMind target sensor)
- Outside: `sensor.outdoor_temperature`, **not an adaptive running mean**.

The output entity ID is assigned by Home Assistant from the chosen device name. No output
consumer is created in Learning disabled or Manual external control. RoomMind and ClimateSync
configuration are never changed. Source/mode changes are available through Options. Changing
the room/outside source, mode or valve binding resets the restored model binding.
Tuning usable opening minima alone no longer discards learned data.

## Provenance and maintenance

The `vendor/` subtree comes from commit
[`6a131df56cf3cfe2974f78f55f33ce8a095076b3`](https://github.com/KipK/vtherm_smartpi/commit/6a131df56cf3cfe2974f78f55f33ce8a095076b3).
See `custom_components/smartpi_calculator/UPSTREAM.json` for original hashes and
`NOTICE`/`LICENSE` for attribution and licensing. These also travel with the component.
No source history or authorship is claimed by the new host. The only adaptation inside
upstream source replaces the HVAC-mode module's VTherm logger with standard Python logging.
The original integration handler, registration, device registry helpers and actuator code are
not imported. Logical cycles replace the **bookkeeping** callbacks, not a physical PWM driver.
Pump protection and any optional physical valve linearization belong outside this wrapper.

This prototype does not port the original integration's calibration/reset service UI or
automatic-calibration supervisor callbacks; it exposes the core bootstrap and learning path.
It is therefore **not a claim of full VTherm runtime parity or proven heating performance**.
The technical Home Assistant domain remains `smartpi_calculator`. Keep the source pin,
notices and hashes when updating the vendored core; do not treat a version bump as proof
of equivalent control behavior.

## Local validation

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python -m ruff check custom_components/smartpi_calculator tests scripts
```

The tests check runtime ownership and numerical behavior, including a holding demand at
the setpoint, bootstrap hysteresis, invalid inputs, reload persistence, exclusive mode
migration, supported-step complementary writes, partial/unavailable feedback, rate-limited
retries, per-radiator rescaling, normalized learning, closing and no writes outside Direct
mode. Passing local tests is not a physical
heating validation. A source publication does not itself install or connect the output.

Verified locally on 2026-10-19 with Python 3.14.4 / Home Assistant 2026.10.1:
184 tests pass; Ruff, format checks and compileall pass. All 35 pinned vendor hashes match.
An isolated temporary HA instance loads the
actual integration and all fifteen sensors, reports unavailable on invalid input, recovers,
restores integral state across reload, and unloads. Configuration tests exercise the
current HA `probatio` schema API (a core dependency in this baseline).
A synthetic 24-hour heating/cooling run reaches both bootstrap and stable phases and
learns heating/cooling samples; this is adapter evidence, not a radiator performance test.
One dependency warning concerns aiohttp application inheritance, not a failing test.
The 0.2.1 tests additionally check default/custom bootstrap thresholds (including zero OFF),
invalid settings, unchanged stable PI output, restored model binding, all ten real HA sensors,
and diagnostic failure visibility. Publishing this release does not install it or change
live Home Assistant settings.

The 0.2.2 tests additionally check signed PI-contribution values, effective predictive
increments after suppression/clamping, unknown contributions outside PI, clearing on
interruption, and unchanged PI output/state while observing the calculation. This release
also removes the previously documented duplicate attributes: migrate attribute-based
templates/dashboards to the existing dedicated sensors. Model coefficients and Kp/Ki
remain attributes. The control law is unchanged; optional per-radiator usable-range settings
are applied outside the core, with model-binding checks as described above.

The 0.2.3 fix removes the overly broad minimum-change reset introduced in 0.2.2.
It preserves sample histories across range tuning and migration from 0.2.2's extended
bindings, while keeping source/mode/actuator identity checks. An update cannot recreate
histories already overwritten: recovery requires an actual earlier stored model, not
diagnostic counters or fabricated samples. No release or live recovery is implied by a
source commit.
Regression tests compare retained estimator, PI, deadtime, gains and feed-forward state
against an ordinary restore, including returning a minimum to zero and a second save/reload.
Isolated HA lifecycle tests change minima through the actual Options update listener and
verify unload/save, reload, live diagnostic sensor values and persisted sample histories.
They also cover unchanged outlier rejection before cooling convergence and the existing
drift-recentering path after accepted cooling observations make it eligible. This does
not guarantee that a new physical valve range will automatically re-identify in practice.

The 0.2.4 change adds only the Unbounded PI demand diagnostic sensor and its English/Dutch
names. Tests cover negative, zero and above-100 output, invalid/missing terms, finite-sum
overflow, a single cached read without mutation, both languages in real isolated HA,
unavailable/recovery and retained sensor IDs. Control/model/storage code and pinned
vendor files are unchanged; no settings migration or model reset is introduced.
