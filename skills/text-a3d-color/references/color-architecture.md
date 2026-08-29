# Color architecture and manufacturing

Color is a geometry and assembly decision, not a renderer decoration.

## Region topology

Use one of these interface patterns deliberately:

- **parent split** for bands or large material zones; pass `parent=` to
  `export_regions()` so volume coverage is audited
- **inset** for screens, labels, and flush panels; cut the footprint from the
  receiving region and give the insert a controlled depth
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
- **shallow inset** for contrasting filled recesses, screens, labels, and
  engraved marks
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

Do not merge screen, control, logo, or material colors merely to reduce purge
without recording the compromise.

## Closed-loop verification

`export_regions()` checks region validity, overlap, parent coverage,
cross-checks the intent's region names/colors, exports `NAME.stl`,
`NAME.3mf`, `NAME-assemble.step`, `NAME-display.glb`, the material plan, and
artifact hashes. It may write hidden internal print-pose region meshes for 3MF
packing and debugging plus semantic-pose region meshes for colored visual
review, but they are not user deliverables.
The default `NAME.3mf` package mode is `co_print_body`: one top-level mesh
build item stores the named color regions with per-triangle colors and region
metadata. Use `separate_parts` only for real separately printed components with
physical assembly interfaces.
`export_3mf.py` stores a shared palette, writes region metadata, and reads the
archive XML back.
`assembly_check.py` compares the expected region names/colors against what is
actually stored in the 3MF. Optical transmission remains region metadata
because RGB readback cannot prove real material behavior.

The clean `NAME.stl` fallback drops color assignments. If the visible feature
must survive single-material slicing, encode it as real parent geometry:
engraving, recess, relief, raised texture, or an intentional shallow groove.
Do not fill an identity-bearing recess completely with a color insert if the
STL fallback is expected to show the recess.

Use `step_check.py` on `NAME-assemble.step` for OCCT-backed master validation.
That check proves CAD readability and shape structure; it does not prove Bambu
print placement, display color, or support behavior.

The colored semantic five-view render is still mandatory: archive correctness
cannot detect a geometrically misplaced color boundary, and print-pose previews
cannot prove the object looks right in its semantic frame.
