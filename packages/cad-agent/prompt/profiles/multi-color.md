# Amagine3D multi-material enclosure workflow

This profile produces a geometry-backed color plan for multi-filament printing.
Every color boundary is represented by a real solid boundary. The deliverable
contains one STL per frozen region, a colored 3MF, and a best-effort STEP
assembly.

## Design contract

Follow the dimensional authority and coordinate convention in this profile and
the shared printability, moving-mechanism and parameter rules in the build123d
authoring guide. The design brief must also contain a frozen color-region plan.
For an opening/closing design, every color region must belong to the frozen
moving or stationary body group of each mechanism so the Worker can move all
regions of a physical part together and audit the exported STEP geometry.

Each region has a stable machine ID, display name, color, feature list and
expected connected-component count. Generated Python uses the machine ID as its
dictionary key. Display names never become source identifiers.

Prefer height-separated colors when the design permits them because they reduce
filament swaps and purge waste. For side-by-side colors, split regions from a
shared parent solid so their boundaries mate without volumetric overlap.

## Source contract

Write one complete `model.py` and import host operations from `amagine_cad`.

```python
from build123d import Align, Box
from amagine_cad import (
    bevel_edges_checked,
    observe_feature,
    publish_color_model,
    round_edges_checked,
    subtract_checked,
)
```

Build the complete geometry, derive the color solids, then finish with exactly
one `publish_color_model` call.

Freeze exact interface and datum dimensions as `featureChecks`, and call
`observe_feature` with each matching ID before booleans erase the cutter or
construction solid. Record reserved insertion, cable, antenna and travel
volumes with `feature_type="keep-out"`; the Worker checks them against every
published color region.

```python
publish_color_model(
    {
        "shell": (shell, "#20242b"),
        "mark": (mark, "#ff5a36"),
    },
    "sensor-enclosure",
    out_dir="cad_out",
)
```

The dictionary keys must exactly equal the frozen region IDs. Colors use
six-digit hexadecimal notation. Regions may touch at their boundaries but must
not share more than 0.01 cubic millimetres of volume.

## Required evidence

The host checks every region for mesh integrity and connected-component count,
checks the assembly dimensions, checks pairwise overlap, and reopens the 3MF to
confirm its object count. When mechanisms are declared, it also reopens each
region STEP and checks the complete sampled motion and declared running
clearances. Frozen feature measurements and observed keep-outs are deterministic
QA requirements too. Repair any failed requirement before finishing.
