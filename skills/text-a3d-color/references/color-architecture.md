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
- **mechanical insert** for parts printed separately; add tolerance and record
  assembly direction

Regions may touch at faces but may not overlap volumes. Avoid paper-thin color
skins that disappear in slicing. For nozzle-based FDM, make visible insets at
least one practical layer high and small strokes at least one extrusion width.

## Palette planning

Run `palette_plan.py` after `reference_analyze.py` when reference colors exceed
available filaments. Use `--keep` for rare identity colors. Treat its weighted
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

`export_regions()` checks region validity, overlap, optional parent coverage,
and writes artifact hashes. `export_3mf.py` stores a shared palette and reads the
archive XML back. `assembly_check.py` compares the expected region names/colors
against what is actually stored in the 3MF.

The colored four-view render is still mandatory: archive correctness cannot
detect a geometrically misplaced color boundary.
