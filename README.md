# SmartPI Calculator

An unofficial Home Assistant wrapper around the existing SmartPI mathematical
core from [KipK/vtherm_smartpi](https://github.com/KipK/vtherm_smartpi).
No VTherm integration is required. This is not an original or rewritten PI algorithm.

**Experimental release (`0.2.0`).** It computes a heating-demand percentage and can
optionally write explicitly selected valve endpoints. Start with **Learning disabled**.

Repository: [Patrick1610/SmartPI-Calculator](https://github.com/Patrick1610/SmartPI-Calculator).

## Boundaries

- Reads **room temperature**, **target temperature**, and **current outdoor temperature**
  from three selected numerical entities. No TRV-temperature dependence.
- Fixed HEAT mode; periodic updates every 30 seconds, logical bookkeeping cycles of 10 minutes.
- Publishes **Heating demand**, 0–100%, and five diagnostic sensors.
- Publishes an unavailable sensor on invalid input; never substitutes a fictitious 0 °C.
- Saves learned model/PI state with Home Assistant storage and restores it after reload/restart.
- No climate entity, schedules, target-writing automations, valve curve, or boiler/pump
  control. Only Direct valve control writes `number.set_value` to selected endpoints.
  Nothing is installed into an existing HA automatically.

The original control law, bootstrap, deadband, integral management and learning rules are
retained. Reaching the setpoint is **not** an additional command to shut the output off.
In the stable phase, feed-forward and retained integral can maintain a holding demand.
The original landing governor can also reduce the demand early. During bootstrap, the
upstream hysteresis may request full output and continue above target. Do not mistake this
for a thermostat that immediately goes to zero at equality.

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
For Sonoff-style complementary endpoints, output `u` requests opening `u`, closing `100-u`.
Both endpoints are rounded onto a common supported step. These values are limits/commands,
not measured physical positions. Other actuator semantics are not supported.

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

Its attributes expose the selected input temperatures (converted to °C), control phase,
learning/assumed-application status, PI gains, thermal-model coefficients and accepted
heating/cooling sample counts. Configuration diagnostics can also be downloaded from
Home Assistant. Source entity IDs are redacted in that download.

Five enabled-by-default diagnostic sensors show **Used room temperature**, **Used target
temperature**, **Used outdoor temperature**, **Control phase** and **Learning status**.
Temperatures are the actual inputs of the latest successful calculation, stored internally
in °C and displayed in HA's temperature unit. All six sensors become unavailable together
when calculation fails. Learning status is `observing`, `paused` or `enabled`; enabled is permission
to learn under assumed 1:1 application, not proof that a sample has been accepted. Its
attributes include accepted heating/cooling sample counts and the feedback assumption.
Demand attributes also expose the selected control mode and valve status (`starting`,
`pending`, `failed`, `unavailable`, `confirmed`, or `not_configured`). In direct mode, `enabled` means
numeric feedback permits learning, not that measured thermal delivery has been confirmed.
The integration is registered as a calculation service, visible under **Devices & services
→ Integrations**, rather than only under Helpers.

A fresh, untrained observer
uses the upstream bootstrap hysteresis; an available percentage alone does not prove
that a learned PI model is active or that heat is physically being delivered.

## Setup

Install through HACS as a **custom repository**: add
`https://github.com/Patrick1610/SmartPI-Calculator` with type **Integration**, download
release `v0.2.0`, and restart Home Assistant. Then select **Settings → Devices & services
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
the room/outside source, mode, or valve binding resets the restored model binding.

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
retries, closing and no writes outside Direct mode. Passing local tests is not a physical
heating validation. A source publication does not itself install or connect the output.

Verified locally on 2026-10-15 with Python 3.14.4 / Home Assistant 2026.10.1:
51 tests pass; Ruff and compileall pass. An isolated temporary HA instance loads the
actual integration and all six sensors, reports unavailable on invalid input, recovers,
restores integral state across reload, and unloads. Configuration tests exercise the
current HA `probatio` schema API (a core dependency in this baseline).
A synthetic 24-hour heating/cooling run reaches both bootstrap and stable phases and
learns heating/cooling samples; this is adapter evidence, not a radiator performance test.
One dependency warning concerns aiohttp application inheritance, not a failing test.
