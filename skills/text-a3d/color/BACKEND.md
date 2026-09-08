# Manufactured-color backend

This is the internal backend for the root intent, semantic scene, and unified
build report, not a separate workflow. Read it after `a3d guide color` only when
one physical part has multiple permanent material regions or uncommon region
topology. Lighting, background, and transient display content remain visual
appearance.

## Internal backend selection

The semantic scene selects the implementation per part:

| Physical representation | Manufactured color implementation |
| --- | --- |
| BRep part, colors follow whole-part boundaries | root `export_part()` / `export_assembly()` |
| BRep part, several regions inside one body | `color.cad_helpers.export_regions()` |
| Display-only color | display GLB only; exclude from manufacturing |

These are internal compilers, not alternative workflows.

Root BRep exporters distinguish appearance from manufactured color. Every 3MF
assignment owns a closed printable whole part or volumetric region; triangle
paint alone never qualifies. A single unpartitioned part therefore has one
whole-part material assignment and remains a single-material print—it is not
reported as multicolor. Real internal multicolor uses `export_regions()` and
meaningful volumetric boundaries. A multipart enclosure can use its existing
physical part boundaries as manufacturing color assignments, so
`export_assembly()` emits stable, distinct proposed whole-part materials when
no palette was declared. Declared colors remain authoritative, and callers may
pass `part_colors` to verify them.

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

## Package and material plan

3MF objects and material properties come from the same compiled physical
geometry as STL and display GLB. The material plan records:

- part and region IDs;
- RGB color;
- whether the color was user-declared or proposed;
- 3MF property/object mapping;
- package mode and plate transform.

It also carries one `sourceBindings[]` entry per assignment. Use
`intent-color-region` with the exact intent region name, `scene-part-material`
with the exact material ID explicitly bound by a scene part, or
`scene-part-appearance` with the exact part ID when the part has no material
binding. The latter two are whole-part proposed sources. Validate all three
against the hash-bound intent and scene; never accept an unbound or duplicated
source ID.

Use `export_regions()` for multiple internal regions in one BRep body and
`export_assembly()` for separate BRep parts with whole-part color assignments.
Region IDs, owners and colors must match intent exactly. Tessellated 3MF region
objects remain derivatives of the BRep material volumes.

Run independent 3MF readback and verify object count, build items, region
coverage, property IDs, colors, and unit millimetres. RGB readback proves stored
color metadata and its binding to printable geometry.

## QA and visual evidence

Color QA extends the unified `evidence-a3d-build/v1` report with region topology,
clean-body mesh, material mapping, and 3MF readback. During the main workflow's
visual gate, treat missing, shifted, floating, or visually merged material
boundaries as failures even when the 3MF archive is syntactically valid.
