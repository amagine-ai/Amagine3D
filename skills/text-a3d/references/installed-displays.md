# Installed components and optional preview references

Use this reference when an installed screen or other purchased component affects
the manufactured geometry or assembly preview. `display.glb` names the assembly
preview, not an electronic screen or a requirement to manufacture one.

For a functional installation, derive the necessary aperture, keepout, support
and retention from a parameterized component envelope. Share installation datums
and dimensions across the receiving geometry, opening and optional visible
reference. A screen needs an unobstructed viewing region; an opaque printed
surrogate does not implement that function. Develop insertion and service access
as needed. `design-review.md` covers these relationships; `a3d guide multipart`
covers separately manufactured parts. Only a request limited to appearance can
use a shallow visual seat alone. Unspecified component dimensions do not imply
that scope.

Declare actual receiving features in physical intent and construct them in the
part. Purchased modules, glass and transient pixels may be represented by
optional `display-only` nodes. They appear in GLB and stay out of STEP, STL, 3MF,
manufacturing part counts and booleans. A printable bezel, cover or dummy is a
manufactured part only when its manufacture is part of the design; preview
appearance alone is not that decision.

Use `bind_display_component` to bind a BRep solid/face or a mesh without manually
writing source meshes and scene JSON. `physical_feature_ref` names the relevant
intent-backed aperture, seat, support or other receiving feature on the same
owning part. This is a semantic association; it does not prove installation.

```python
from geometry_binding import bind_display_component

reference = bind_display_component(
    node_id="module-reference", feature_id="reference/module",
    physical_feature_ref="module-mount", shape=module_envelope,
    path=OUT / ".references" / "module.ply",
    appearance={"baseColor": "#111417", "roughness": 0.18},
)
```

Nest the reference under its receiving part in `write_scene(...)`; never put it
in `export_part`, `export_assembly` or `export_regions` manufacturing inputs.
Omitting the reference leaves the manufactured geometry and installation checks
unchanged. The assembly viewer can hide marked references without changing pose;
inspect the exposed structure as well as the assembled appearance.

`examples/installed_module_intent.py` and `installed_module_build.py` demonstrate
a complete configurable module installation, including a real window, shared
datums, support, removal access and independent installation evidence. Use the
structure as an API example, not a default product shape or component size.
`installation-checks.md` explains the applicable geometric proofs and their limits.
