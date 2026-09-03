# Installed display components

Read this reference only when the requested product contains an LED/LCD, screen,
windowed module, or another non-manufactured component that must appear in the
assembly display.

Derive the physical enclosure consequences before visual decoration: a visible
aperture, a rear component keepout or seat with clearance, and any printable
retention features. Use one component envelope and one screen datum for all of
them. The full module keepout is not the visible opening, and the visual surface
is not a cutting tool. Apply the general installed-item assembly rule from the
main skill: provide a feasible insertion path, and when the module enters an
enclosed volume or needs service access, route to the removable-cover guidance
in `multipart-connections.md` rather than sealing the body around it.

When the user has not chosen another closure, use this adjustable assembly
starting point: select one service opening, let an existing base or a dedicated
panel close it, add a locating seam, and retain it with the connection selected
from `multipart-connections.md`. The cover location, split line, locator,
fastener family, count, and access direction remain design decisions. Do not
apply this construction when the item only passes through or follows a surface,
and always replace it with an explicit user-requested closure.

Declare the aperture, module keepout/seat, and printed retainer as physical
intent features owned by the receiving manufactured part. Represent the glass,
active pixels, or transient content as a `display-only` `displayComponent`
linked through `physicalFeatureRef` to the aperture feature. Include that node in
the display GLB, but exclude it from STEP, STL, 3MF, manufacturing part counts,
and manufacturing booleans. Use a physical part only for an explicitly printable
dummy, lens, or bezel.

The physical cutters and display plane share their center, normal, and component
envelope parameters in source. BRep `export_part(...)` and
`export_assembly(...)` consume the scene's display-only nodes for the display
GLB without adding them to their physical parts argument. A typical compiled
scene fragment is:

```json
[
  {
    "id": "screen-window-cutter",
    "partId": "housing",
    "featureId": "screen/window",
    "role": "cutter",
    "operation": "subtract",
    "recipe": {
      "kind": "sourceMesh",
      "parameters": {"sourceMesh": "screen-window-tool.stl"}
    }
  },
  {
    "id": "screen-module-keepout-cutter",
    "partId": "housing",
    "featureId": "screen/module-keepout",
    "role": "cutter",
    "operation": "subtract",
    "recipe": {
      "kind": "sourceMesh",
      "parameters": {"sourceMesh": "screen-module-keepout-tool.stl"}
    }
  },
  {
    "id": "screen-active-surface",
    "partId": "housing",
    "featureId": "display/screen-active-surface",
    "role": "display-only",
    "operation": "none",
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

Scene validation requires each display component's `physicalFeatureRef` to name
an intent-backed physical feature on the same owning part. Build input binding
repeats that check and ensures display-only nodes do not broaden the immutable
manufacturing request.
