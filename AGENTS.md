# SmartPI Calculator

- Experimental, sensor-only Home Assistant integration. No service calls, actuator writes,
  climate platform, boiler control, or implicit installation into a live HA instance.
- `custom_components/smartpi_calculator/vendor/` is upstream code, not a new algorithm.
  Preserve its provenance and license. Only the documented HVAC logging import is adapted.
- Never infer actual application from a computed demand. Learning defaults off; the optional
  `output_applied` setting is the user's explicit assertion of external 1:1 application.
- No `temperature >= target -> 0` rule and no hydraulic pump-overrun policy in the harness.
- Tests: `.venv/bin/python -m pytest`; lint only authored files, excluding `vendor/`.
- README.md records architecture, limitations, setup and validation. No release, GitHub push,
  HA restart or installation without a separate instruction.
