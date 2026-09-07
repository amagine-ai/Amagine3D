# Installed display components

Read this reference only when the requested product contains an LED/LCD, screen,
windowed module, or another non-manufactured component that must appear in the
assembly display.

Derive the physical enclosure consequences before visual decoration: a visible
aperture, a rear component keepout or seat with clearance, and any printable
retention features. Use one component envelope and one screen datum for all of
them. The full module keepout is not the visible opening, and the visual surface
is not a cutting tool. Provide a feasible insertion path. When the module enters
an enclosed volume or needs service access, use `a3d guide multipart` and read
`multipart-connections.md` only if its serviceable-enclosure closure applies.
Do not create a removable enclosure for an item that only passes through or
follows a surface.

Declare the aperture, module keepout/seat, and printed retainer as physical
intent features owned by the receiving manufactured part. Represent the glass,
active pixels, or transient content as a `display-only` `displayComponent`
linked through `physicalFeatureRef` to the aperture feature. Include that node in
the display GLB, but exclude it from STEP, STL, 3MF, manufacturing part counts,
and manufacturing booleans. Use a physical part only for an explicitly printable
dummy, lens, or bezel.

The physical cutters and display plane share their center, normal, and component
envelope parameters in source. BRep `export_part(...)`, `export_assembly(...)`,
and `color.export_regions(...)` consume the scene's display-only nodes for the
display GLB without adding them to their physical parts argument. Bind the actual cutter
objects rather than describing their dimensions again:

```python
from geometry_binding import bind_brep_feature

nodes = [
  bind_brep_feature(
    node_id="screen-window-cutter",
    feature_id="screen/window",
    role="cutter",
    shape=window_cutter,
    path="screen-window-tool.stl",
  ),
  bind_brep_feature(
    node_id="screen-module-keepout-cutter",
    feature_id="screen/module-keepout",
    role="cutter",
    shape=module_keepout_cutter,
    path="screen-module-keepout-tool.stl",
  ),
  {
    "id": "screen-active-surface",
    "featureId": "display/screen-active-surface",
    "role": "display-only",
    "physicalFeatureRef": "screen/window",
    "recipe": {
      "kind": "displayComponent",
      "parameters": {
        "sourceMesh": "screen-active-surface.ply",
        "appearance": {"baseColor": "#111417", "roughness": 0.18}
      }
    }
  }
]
```

Nest these nodes under the receiving part passed to `write_scene(...)`; it
derives `partId` and `operation`.

Scene validation requires each display component's `physicalFeatureRef` to name
an intent-backed physical feature on the same owning part. Build input binding
repeats that check and ensures display-only nodes do not broaden the immutable
manufacturing request.
