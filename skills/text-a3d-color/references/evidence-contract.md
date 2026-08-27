# Color evidence contract

Write `<name>_intent.json` before geometry and validate it with this
skill's `intent_contract.py`.

```json
{
  "schema": "evidence-color-intent/v3",
  "part": "product-name",
  "task_mode": "reference-reproduction",
  "representation": "full-3d",
  "coordinate_system": {
    "x_positive": "right",
    "y_positive": "back",
    "z_positive": "top",
    "front": "y-min",
    "back": "y-max",
    "left": "x-min",
    "right": "x-max",
    "bottom": "z-min",
    "top": "z-max"
  },
  "reference_files": [
    {"path": "/absolute/reference.png", "sha256": "...", "role": "appearance"}
  ],
  "dimensions_mm": {
    "x": {"value": 140, "source": "inferred", "confidence": "medium"},
    "y": {"value": 60, "source": "inferred", "confidence": "low"},
    "z": {"value": 30, "source": "reference", "confidence": "medium"}
  },
  "features": [
    {
      "id": "fastener-clearance",
      "kind": "hole",
      "face": "top",
      "direction": "through-Z",
      "edge_crossing": "forbidden",
      "evidence": "The assembly specification requires a through fastener.",
      "acceptance": "Clear diameter 3.4 mm; continuous through the housing wall."
    },
    {
      "id": "accent-inset",
      "kind": "recess",
      "face": "front",
      "direction": "-Y",
      "edge_crossing": "forbidden",
      "evidence": "The contrasting front insert is identity-bearing.",
      "acceptance": "Centered inset; printable boundary and no overlap with housing."
    }
  ],
  "color_regions": [
    {
      "name": "housing",
      "hex": "#E8E4DC",
      "purpose": "main enclosure",
      "boundary": "complete parent shell",
      "evidence": "warm light housing in reference"
    },
    {
      "name": "accent",
      "hex": "#171A1D",
      "purpose": "identity-bearing front insert",
      "boundary": "front inset rectangle",
      "evidence": "dark contrasting insert region"
    }
  ],
  "palette_reduction": {
    "source_colors": 7,
    "filament_limit": 4,
    "plan": "product_palette.json",
    "deliberate_merges": ["two photographic highlights merge into housing"]
  },
  "printability": {
    "profile": {
      "path": "product-name_printer-profile.json",
      "sha256": "..."
    },
    "build_axis": "+Z",
    "bed_contact": "z-min",
    "print_package_mode": "co_print_body",
    "support_policy": "support-free",
    "minimum_wall_target_mm": 0.9,
    "critical_features": ["fastener-clearance", "accent-inset"]
  },
  "visual": {
    "required": true,
    "reference_view": "front",
    "landmarks": ["accent inset centered", "top control distinct", "right knob distinct"]
  },
  "assumptions": []
}
```

When the user asks to replicate, reproduce, or exactly match a named real,
catalog, branded, or fictional object, preserve that identity as the target.
Use `reference-reproduction` when supplied or discoverable evidence supports it.
If no reference evidence is supplied, choose `reference-inspired` or
`recognizable-form`, record inferred landmarks, dimensions, and semantic colors,
and report that the result is inspired by the named object rather than an exact
replica.

Color sampled from a photograph is evidence, not automatically a filament.
Separate semantic regions first; reduce shades inside each semantic region
second. Preserve rare colors when they encode a logo, control, status, or
material boundary. Record every deliberate merge.

The contract must distinguish permanent printed color from transient display
content. A real LED/LCD screen is usually one physical screen region; reproduce
individual pixels only when the user wants a static decorative face or mosaic.

Use `material.transmission` only when optical behavior changes the geometry or
visual promise; omitted material defaults to opaque. A region may preserve a
user-specified filament, but the skill should not invent one. `export_regions()`
cross-checks contract names and colors and writes a material plan that records
region metadata without treating RGB readback as proof of real material choice.
Use `color_regions[].continuity: "continuous-core"` when a region represents a
handle, shell, post, tab, rib, or other core that must remain one solid.
Surface accents on that core should be outer shells, shallow insets, raised
overlays, or shallow filled grooves rather than full-depth color blocks that
split the core.

Declare functional and identity-bearing requirements independently in
`features`. Every item needs evidence and a concrete acceptance condition.
Use flat semantic fields directly on features when placement matters:
`kind`, `face`, `direction`, and `edge_crossing`. Ports, holes, slots,
cutouts, windows, cavities, and recesses must name a semantic face and
direction rather than relying on prose offsets.
Every `printability.critical_features` ID must reference that list and must
later appear as a named `observe()` record or checked operation in the v4
build report. For routed cavities, record a representative cross-section as a
separate named feature; the overall bounding box of a bent or compound cutting
tool does not prove local clearance.

Use the fixed object semantic coordinate system in every design. `+X` is the
user's right, `+Y` is the object's back, and `+Z` is the object's top. Front is
`Y-min`; bottom is `Z-min`. Define every port, window, switch, and color
boundary by semantic face and direction first. A bottom opening is valid when
it is explicitly bottom-facing; an accidental cut spanning the front/bottom
edge fails the design contract.

The printability profile must come from this skill's `bambu_profile.py`. Its
hash locks the machine, selected tool, nozzle, process, bed, feature floor,
wall target, and support threshold. Use `NAME.3mf` for preferred multi-color
print package QA and archive readback. `printability.print_package_mode`
defaults to `co_print_body`, where one top-level 3MF mesh build item stores
all color regions with per-triangle colors and region metadata. Use
`separate_parts` only for real multipart prints with physical assembly
interfaces. Use `NAME.stl` for the clean whole-body
manufacturing mesh QA that matches the single-color skill's STL checks. Hidden
`NAME-region-REGION.stl` meshes are internal topology inputs only, not
printable part deliverables. If a feature must be visible in the STL fallback,
encode it as parent geometry rather than relying only on color assignment.
Use `support-free` only when it does not change the requested geometry;
otherwise preserve the object and set `supports-required` or disclose support
warnings. Support avoidance must not flatten an underside, remove back-side
details, or turn a full-3D object into a relief.
Use `NAME-assemble.step` for OCCT-backed CAD master checks; STEP checks do not
replace print-bed mesh QA. Keep `NAME-assemble.step` and `NAME-display.glb` in
semantic object orientation so replica review is not confused with print
placement. Use `NAME-display.glb` as the user-visible colored display model;
GLB does not replace CAD topology QA.
Hidden print-pose region meshes support 3MF packing and package debugging;
hidden semantic-pose region meshes support colored visual review. Do not judge
identity, landmark placement, or reference-view matching from print-pose region
meshes.

Allowed task modes are `specification`, `reference-reproduction`,
`reference-inspired`, `recognizable-form`, and `inspect`. Matched visual views
may be `front`, `side`, `top`, `bottom`, or `isometric`; use `bottom` when the
appearance-bearing face is intentionally printed at Z0.
