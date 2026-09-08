# Installed display components

Use this reference when a screen or other non-manufactured visible component is
represented alongside the enclosure geometry. Match the engineering detail to
the requested design.

A visual concept can define its visible outline and a real shallow seat or recess
in the host surface, with proposed dimensions exposed as parameters. For a module
whose installation matters to the request, derive the necessary aperture,
keepout, support and retention from its component envelope. Develop insertion
and service access when that assembly needs them; `design-review.md` covers those
relationships and `a3d guide multipart` covers separately manufactured parts.

Declare the modeled aperture, seat or cavity as a physical intent feature of the
receiving part and apply its actual cutter. Represent the glass, active pixels or
transient content as a `display-only` `displayComponent`, linked through
`physicalFeatureRef` to that feature. The display node appears in GLB and stays
out of STEP, STL, 3MF, manufacturing part counts and booleans. Any requested
printable dummy, lens or bezel remains physical geometry.

Share the host-surface frame, outline and placement controls between the physical
feature and visible surface. A flat module can use a tangent plane; a conforming
surface can follow the host profile. Keep the component keepout distinct from
the visible opening. Both the BRep helper and hybrid compile paths consume the
scene's display-only nodes for GLB. This minimal binding uses a seat cutter;
add a separate module keepout or retainer when the intended installation calls
for one:

```python
from geometry_binding import bind_brep_feature

nodes = [
  bind_brep_feature(
    node_id="screen-seat-cutter",
    feature_id="screen/seat",
    role="cutter",
    shape=seat_cutter,
    path="screen-seat-tool.stl",
  ),
  {
    "id": "screen-active-surface",
    "featureId": "display/screen-active-surface",
    "role": "display-only",
    "physicalFeatureRef": "screen/seat",
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
