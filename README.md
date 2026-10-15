# SmartPI Calculator

An unofficial, sensor-only Home Assistant wrapper around the existing SmartPI mathematical
core from [KipK/vtherm_smartpi](https://github.com/KipK/vtherm_smartpi).
No VTherm integration is required. This is not an original or rewritten PI algorithm.

**Experimental prototype (`0.1.0-dev.0`).** It computes a heating-demand percentage;
it does not control your heating system. Start in observation mode.

Repository: [Patrick1610/SmartPI-Calculator](https://github.com/Patrick1610/SmartPI-Calculator).

## Boundaries

- Reads **room temperature**, **target temperature**, and **current outdoor temperature**
  from three selected numerical entities. No TRV-temperature dependence.
- Fixed HEAT mode; periodic updates every 30 seconds, logical bookkeeping cycles of 10 minutes.
- Publishes one **Heating demand** sensor, 0–100%, with model/learning diagnostics.
- Publishes an unavailable sensor on invalid input; never substitutes a fictitious 0 °C.
- Saves learned model/PI state with Home Assistant storage and restores it after reload/restart.
- No climate entity, device writes, service calls, schedules, target-writing automations,
  valve curve, or boiler/pump control. Nothing is installed into an existing HA automatically.

The original control law, bootstrap, deadband, integral management and learning rules are
retained. Reaching the setpoint is **not** an additional command to shut the output off.
In the stable phase, feed-forward and retained integral can maintain a holding demand.
The original landing governor can also reduce the demand early. During bootstrap, the
upstream hysteresis may request full output and continue above target. Do not mistake this
for a thermostat that immediately goes to zero at equality.

## Learning versus observing

Initially, learning is **off**. You can inspect the computed demand without claiming it
has heated the room. With an empty model, this remains the upstream bootstrap calculation;
it will not magically learn a stable model while its demand is not being applied.

In integration **Options**, enable “Demand is applied externally 1:1” only after arranging
the external application yourself. The host then retains the previous published command
as the assumed applied command, including its time-weighted history. This is an explicit
assumption, not measured power or a physical delivery acknowledgement. We do not equate
40% valve command with 40% thermal watts. Variable supply-water temperature, hydraulic
competition, lost commands or a boiler outage can violate the simplified thermal model.
Emma's global demand is neither a room-power input nor a supply-temperature correction.

No extra `room >= target -> 0` rule, positive-delta-only learning gate or pump-overrun hold
has been invented. Cooling observations with zero command are needed for heat-loss learning.
Minimum hydraulic flow and boiler pump overrun are outside this integration's scope.
Confirm installation suitability before actuator use.

## Output and diagnostics

The single `Heating demand` sensor publishes **0–100%**. This is a normalized command,
not measured valve position, delivered heat or boiler modulation.

Its attributes expose the selected input temperatures (converted to °C), control phase,
learning/assumed-application status, PI gains, thermal-model coefficients and accepted
heating/cooling sample counts. Configuration diagnostics can also be downloaded from
Home Assistant. Source entity IDs are redacted in that download.

No extra diagnostic entities are required to use the output. A fresh, untrained observer
uses the upstream bootstrap hysteresis; an available percentage alone does not prove
that a learned PI model is active or that heat is physically being delivered.

## Setup

This is a source publication, not a stable release or inclusion in HACS's default store.
The steps below describe manual installation of the experimental component.

Requires Home Assistant **2026.10 or newer**. Copy `custom_components/smartpi_calculator/`
into HA's `custom_components/`, restart HA when appropriate, then add **SmartPI Calculator**.
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
consumer, RoomMind change, TRV change or ClimateSync change is created. Source changes are
available through Options. Changing the room/outside source resets the restored model binding.

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
the setpoint, bootstrap hysteresis, invalid inputs, reload persistence and absence of
actuator-service calls. Passing local tests is not a live HA installation or physical
heating validation. A source publication does not itself install or connect the output.

Verified locally on 2026-10-15 with Python 3.14.4 / Home Assistant 2026.10.1:
32 tests pass, Ruff and compileall pass. An isolated temporary HA instance loads the
actual integration and its sensor, reports unavailable on invalid input, recovers,
restores integral state across reload, and unloads. Configuration tests exercise the
current HA `probatio` schema API (a core dependency in this baseline).
A synthetic 24-hour heating/cooling run reaches both bootstrap and stable phases and
learns heating/cooling samples; this is adapter evidence, not a radiator performance test.
One dependency warning concerns aiohttp application inheritance, not a failing test.
