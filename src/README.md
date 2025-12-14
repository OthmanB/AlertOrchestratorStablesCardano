# Source Layout

This directory will contain the orchestrator source modules. For Phase A, only documentation exists; no code changes have been made.

## Layout

- `shared/`
  - Contains copies of minimal modules from `client/`:
    - `config.py`, `greptime_reader.py`, `greptime_writer.py`, `liqwid_client.py`, `gains_calculator.py`, `aggregation.py`, `models.py`, `utils.py`
  - Copy these files exactly as-is in Phase A (no modifications), then adjust imports in Phase B if needed.

- `core/`
  - Orchestrator-specific modules (implemented later):
    - `alert_logic.py` (decision engine)
    - `reference_state.py` (reference t0 handling)
    - `price_source.py` (optional comparison)
    - `io_adapters.py` (decoupling layer)

## Notes

- Do not import from the `client` package after copying; use relative imports within `shared/`.
- `reporting.py` or any chart-related modules are intentionally excluded.
