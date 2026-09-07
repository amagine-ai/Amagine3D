# Color architecture and manufacturing

Color is a geometry and assembly decision, not a renderer decoration.

The root `evidence-cad-intent/v5` document is the authority for region IDs,
owners, and colors.
Region IDs are globally unique. `part` names the owning physical part; it does
not imply that a multipart model has only one region per part. Hybrid scenes
must reproduce the exact region-ID set for every mesh part. Conflicting scene
colors are an error.

Every material assignment is traceable through exactly one `sourceBindings[]`
record. Declared manufactured color uses `intent-color-region` and the exact
intent region name. A whole-part color that exists only in the mutable scene is
proposed and uses either `scene-part-material` with the part's explicit material
ID or `scene-part-appearance` with the real part ID when no material is bound.
The build fails if those sources are missing, duplicated, unknown, or disagree
with the hash-bound intent/scene.

## Region topology

Use one of these interface patterns deliberately:

- **parent split** for bands or large material zones; pass `parent=` to
  `export_regions()` so volume coverage is audited
- **inset** for printable dummy screens, labels, bezels, and flush panels; cut
  the footprint from the receiving region and give the insert a controlled depth
- **raised overlay** for readable text or icons; keep sufficient printable
  stroke width and avoid coincident faces
- **mechanical insert** only for real separately printed parts; design it with
  printable interfaces before treating it as multipart assembly

Regions may touch at faces but may not overlap volumes. Avoid paper-thin color
skins that disappear in slicing. For nozzle-based FDM, make visible insets at
least one practical layer high and small strokes at least one extrusion width.
Use the resolved profile's wall target for structural dividers and internal
walls. `export_regions()` requires `parent=` so the manufacturing body is a
single coverage-checked print shape without internal region-interface faces.
Color regions are not printable assembly parts.

For a continuity-bearing core such as a handle, post, housing shell, hinge
barrel, tab, or load-carrying rib, keep the core's region as one continuous
solid. Do not represent surface bands, stripes, trim, logos, runes, or labels
as full-depth blocks that cut the core into separated pieces. Prefer:

- **outer shell** for circumferential grip bands, rings, collars, and stripes
- **shallow inset** for contrasting filled recesses, printable dummy screens,
  labels, bezels, and engraved marks
- **raised overlay** for readable text, icons, logos, and surface emblems
- **shallow filled groove** when a multi-color insert should sit inside a
  single-material-visible recess

Use `color_regions[].continuity: "continuous-core"` when a region must remain
one solid in the build report. A split continuous-core region is a design
failure even when the unioned parent still covers the complete body.

For replica or exact-match requests, color architecture must preserve the
object-owned form before optimizing purge, bed contact, or support behavior.
Do not make the underside plain, collapse a round handle into a flat bar, or
replace three-dimensional back-side detail with a top-only color overlay unless
the user requested a relief/flat-backed prop.

## Palette planning

Run `palette_plan.py` after `reference_analyze.py` when reference colors exceed
available color channels. Use `--keep` for rare identity colors. Treat its weighted
mapping as a proposed manufacturing palette, then reconcile it with semantic
regions in the contract.

Minimize purge cost after appearance is correct:

1. height-separated changes
2. large contiguous regions
3. face insets or separately assembled inserts
4. dense per-layer mosaics only when identity requires them

Do not merge printable bezel/dummy-screen, control, logo, or material colors
merely to reduce purge without recording the compromise. A real excluded
display visual is not part of the purge plan.

## Closed-loop verification

`export_regions()` checks region validity, overlap, parent coverage,
cross-checks the intent's region names/colors, exports `NAME.stl`,
`NAME.3mf`, `NAME.step`, `NAME-display.glb`, the material plan, and one unified
build manifest. It also writes hidden internal print-pose and semantic-pose
region meshes; each is hash-bound in the manifest even though it is not a user
deliverable.
The `NAME.3mf` package mode is `co_print_body`: every permanent material region
is an independent closed child MeshObject with one object-level palette
assignment. One parent ComponentsObject references exactly those regions and
is the archive's only build item. It is one co-printed physical body, not a
collection of separately installable parts. Per-triangle coloring is forbidden
for this package mode. Real separately printed components use root
`export_assembly()` and `separate_parts`; `export_regions()` rejects that mode.
`export_3mf.py` stores a shared palette, writes exact region-to-object metadata,
and independently imports the archive with lib3mf. Readback must prove the
component graph, region names, object IDs, colors, closed topology, and material
mapping. Missing metadata or an empty region inventory is an error; inspection
never falls back to generic object inventory. Both its Python writer and CLI
require the package mode explicitly; use `package_mode=...` in Python or
`--package-mode co_print_body|separate_parts` on the CLI. Inspection and QA do
not infer a missing mode from archive structure or a build-report fallback.
`assembly_check.py` compares the expected region names/colors against what is
actually stored in the 3MF.

The clean `NAME.stl` fallback drops color assignments. If the visible feature
must survive single-material slicing, encode it as real parent geometry:
engraving, recess, relief, raised texture, or an intentional shallow groove.
Do not fill an identity-bearing recess completely with a color insert if the
STL fallback is expected to show the recess.

Use the root `step_check.py` on `NAME.step` for OCCT-backed master validation.
That check proves CAD readability and shape structure; it does not prove Bambu
print placement, display color, or support behavior.

The colored semantic five-view render is still mandatory: archive correctness
cannot detect a geometrically misplaced color boundary, and print-pose previews
cannot prove the object looks right in its semantic frame.
