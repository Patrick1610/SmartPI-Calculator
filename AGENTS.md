# SmartPI Calculator

- Experimental Home Assistant calculator with optional, explicitly selected direct valve
  control. Only the valve adapter may call `number.set_value` for configured opening/closing
  pairs. No climate platform, climate/boiler writes, or implicit live installation.
- `custom_components/smartpi_calculator/vendor/` is upstream code, not a new algorithm.
  Preserve its provenance and license. Only the documented HVAC logging import is adapted.
- Learning defaults off. Modes are Learning disabled, Manual external control (explicit
  external 1:1 assumption), and Direct valve control (learning pauses without matching
  numeric echoes). Numeric echoes do not prove physical movement, heat or water flow.
- Direct control requires Adaptive mode off and no competing endpoint writers. Round
  complementary endpoints to supported steps, close on bad inputs/unload, and bound retries.
- Keep RoomMind as the target source; do not alter other control-chain components implicitly.
- No `temperature >= target -> 0` rule and no hydraulic pump-overrun policy in the harness.
- Tests: `.venv/bin/python -m pytest`; lint only authored files, excluding `vendor/`.
- README.md records architecture, limitations, setup and validation. No release, GitHub push,
  HA restart or installation without a separate instruction.
