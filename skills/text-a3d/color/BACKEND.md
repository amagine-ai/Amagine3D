# Manufactured-color backend

This directory is an internal backend of `text-a3d`. It is not a separate
Agent workflow and does not own another intent contract. Use the root
`evidence-cad-intent/v4`, semantic scene, profile, build report, repair loop,
visual gate, and delivery rules.

Read this file only when permanent printed color or material belongs to physical
geometry. Lighting, reflection, background, and a transient LED/LCD image are
display appearance, not manufactured color.

## Region semantics

Keep physical parts and color regions independent:

- a part is separately printable and owns manufacturing interfaces;
- a region is a permanent material assignment within one physical part;
- several parts may share a color;
- one part may contain several regions;
- a display-only surface never enters STL or 3MF.

Declare regions once in the root intent and bind them in the semantic scene.
Each region records a globally unique stable ID, owning physical part,
`#RRGGBB` appearance, purpose, boundary evidence, and acceptance. Optional
intent material fields are authoritative: when `filament` or `transmission`
(`opaque`, `translucent`, or `transparent`) is declared, the scene must match
it or omit it so the compiler can propagate it. Do not invent a real filament;
record every unspecified choice as proposed in the material plan.

## Internal backend selection

The semantic scene selects the implementation per part:

| Physical representation | Manufactured color implementation |
| --- | --- |
| BRep part, colors follow whole-part boundaries | root `export_assembly(..., part_colors=...)` |
| BRep part, several regions inside one body | `color.cad_helpers.export_regions()` |
| Mesh part, several volumetric regions | `hybrid_compile.py` region assignments |
| Display-only color | display GLB only; exclude from manufacturing |

These are internal compilers, not alternative workflows.

## BRep internal regions

Use `export_regions()` when validated BRep shapes form permanent material
partitions inside one physical part. Build the complete parent first, then
derive regions. The backend must prove:

- every region is a valid solid;
- regions do not overlap beyond tolerance;
- their union covers the parent within tolerance;
- every intent region appears exactly once;
- colors and material metadata match intent;
- the clean whole-body STL remains valid;
- the 3MF readback preserves region/material assignments.

`co_print_body` produces one physical print body with color/material
properties. Its 3MF contains one closed child MeshObject per volumetric region
and one parent ComponentsObject as the only build item. Every child has an
object-level material assignment; triangle-only surface coloring is invalid.
Hidden per-region STLs are topology evidence, not separate user parts.

## Mesh internal regions

A mesh-master part declares `colorRegions` in the semantic scene. The complete
physical body must be partitioned into validated, closed material volumes, and
each volume belongs to exactly one intent region. A multipart scene may
therefore contain several material regions inside one mesh part while another
physical part has a single whole-part material. `separate_parts` describes the
package of physical parts, not a one-region-per-part restriction. Reject:

- unknown or duplicate region IDs;
- gaps in the physical volume;
- overlapping volumetric regions;
- a region assigned to another part;
- a region ID set that differs from the IDs owned by that part in intent;
- scene material color, filament, or transmission that conflicts with intent;
- display-only material used as manufacturing color;
- 3MF readback that changes region or material assignment.

Per-triangle surface paint describes appearance only. Volumetric regions
describe actual co-printed material bodies. Do not claim manufactured internal
color from surface labels alone.

## Package and material plan

The preferred manufactured-color deliverable is 3MF. Its objects and material
properties come from the same compiled physical geometry as STL and display
GLB. The material plan records:

- part and region IDs;
- display color;
- optical transmission;
- proposed or user-specified material;
- 3MF property/object mapping;
- package mode and plate transform.

It also carries one `sourceBindings[]` entry per assignment. Use
`intent-color-region` with the exact intent region name, `scene-part-material`
with the exact material ID explicitly bound by a scene part, or
`scene-part-appearance` with the exact part ID when the part has no material
binding. The latter two are whole-part proposed sources. Validate all three
against the hash-bound intent and scene; never accept an unbound or duplicated
source ID.

Hybrid compilation binds intent to scene before geometry compilation. For a
part with `colorRegions`, IDs and implicit owners must match intent exactly. A
part without `colorRegions` may bind only one whole-part intent region to its
part material. Multiple internal regions on a BRep master are rejected by the
Hybrid backend and must use `export_regions()`; a root BRep assembly remains
the stricter one-whole-color-region-per-part case.

Run independent 3MF readback and verify object count, build items, region
coverage, property IDs, colors, and unit millimetres. RGB readback proves stored
metadata, not the user's real spool selection.

## QA and visual evidence

Color QA is an internal adapter over the unified
`evidence-a3d-build/v1` report. It checks region topology, clean-body mesh,
profile fit, material mapping, and 3MF readback. It does not introduce a second
build-manifest schema or a second intent.

Render the final semantic display GLB after the manufacturing package is final,
then read the new preview. Review material boundaries as geometry evidence:
missing, shifted, floating, or visually merged regions fail even when the 3MF
archive is syntactically valid.
