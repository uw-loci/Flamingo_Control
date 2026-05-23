# Chamber profiles

Each `*.yaml` in this folder is a STEP-chamber **features file** — the same
format as `../step_chamber_features.yaml`, produced by
`scripts/extract_step_chamber.py` and hand-edited to match the physical part.

A features file carries the chamber geometry **and** its `step_to_stage_transform`,
so each profile is self-contained — switching profiles swaps both.

## Using profiles

Any YAML dropped here appears in the **Chamber profile** dropdown under
*Viewer Controls → STEP Chamber Geometry*. Selecting one rebuilds the 3D chamber
view live — no restart. The choice is remembered between sessions.

The dropdown also lists the config default
(`step_chamber.features_yaml` in `visualization_3d_config.yaml`), so the
shipped 25× chamber is always available even though it lives one level up.

## Adding a chamber

1. Extract: `python3 scripts/extract_step_chamber.py <chamber.STEP> configs/chambers/<name>.yaml`
2. Hand-edit and **recalibrate `step_to_stage_transform`** for that chamber /
   objective.
3. Add an optional top-level `display_name:` for a friendly dropdown label
   (the filename is used if absent).

Full instructions, the transform-calibration procedure, and a reference for
every YAML field are in **`docs/step_chamber_profiles.md`**.

## Scope note

A profile swaps STEP geometry + transform only. Display-volume ranges
(`stage_control.x/y/z_range_mm`) and holder hardware dimensions
(`sample_chamber.*`) remain global in `visualization_3d_config.yaml` — change
those there if a new chamber needs different values.
