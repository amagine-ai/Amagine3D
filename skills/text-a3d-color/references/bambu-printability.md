# Bambu printability for color assemblies

Resolve this skill's printer profile before geometry. The profile fixes the
machine, selected tool, nozzle, standard process, printable polygon, line-width
floor, wall target, and support threshold for the entire evidence run.

## Audit the print package and manufacturing mesh

Color-region meshes are semantic partitions, not printable parts or
independent evidence of how a co-printed object is supported. A downward face
in one region may sit directly on another material. For that reason:

1. Use hidden internal region meshes only for optional topology debugging.
2. Pass `parent=` so `export_regions()` can prove coverage and export a clean
   `NAME.stl`.
3. Run lightweight static print-package QA on `NAME.3mf` for package
   provenance, package mode, unit, the top-level build item, region
   names/colors, dimensions, Z0, and bed fit.
4. Run profile-backed feature, wall, and overhang checks on `NAME.stl`, the
   clean whole-body manufacturing mesh. Treat these printability checks as
   advisory unless they expose a broad process blocker.

`NAME.3mf` is the preferred colored print package. `NAME.stl` is retained as
the clean whole-body mesh for single-color-style manufacturing QA and fallback
printing. Hidden `NAME-region-REGION.stl` files are intermediate meshes, not
final artifacts. The default package mode is `co_print_body`, which writes one
top-level 3MF mesh build item with per-triangle colors and region metadata. If
the design truly needs separate printed parts, set
`printability.print_package_mode` to `separate_parts`; those parts need real
assembly interfaces, not color regions used as a substitute.

## Design targets

- Keep every normal feature at or above `single_line_floor_mm`.
- Use `process_wall_target_mm` for shells, internal walls, dividers, and region
  interface walls. Meeting it proves slicer compatibility, not strength.
- Put co-printed material interfaces on the build plane or on already printed
  material where possible.
- Avoid thin decorative color skins that are below one practical layer or one
  extrusion width.
- Keep continuous-core regions as one solid. Put contrasting surface details on
  outer shells, shallow insets, raised overlays, or shallow filled grooves
  instead of full-depth color blocks that sever handles, housings, posts, tabs,
  or ribs into separate solids.
- Preserve single-material-visible engravings, recesses, reliefs, and raised
  textures in the parent geometry so `NAME.stl` does not lose the feature when
  color assignments are discarded.
- Treat purge reduction as secondary to appearance, region integrity, and
  printable boundaries.
- For internal paths, sockets, fasteners, and installed components, observe a
  representative local clearance feature. Do not use the global bounding box
  of a bent or compound cut tool as proof of its narrowest section.

## Supports and orientation

The profile's support angle is measured upward from horizontal. Prefer a
support-free orientation for the manufacturing assembly only after the requested
shape is preserved. Reorient first; split where the contract permits; add
slopes, chamfers, or arches only when they are faithful to the object or
explicitly accepted as a manufacturing compromise. Do not treat a bridge as
automatically safe, and do not infer support need from an isolated co-printed
region. If support-free printing conflicts with replica fidelity, preserve the
replica and declare supports required or disclose the warning.
Do not optimize for warning-free QA. Repair overhangs only when support-free
output is a user requirement or support demand is excessive enough to threaten
the print process. Ordinary local overhangs should become disclosed support
requirements, not a reason to flatten, move, thicken, simplify, or convert
semantic color regions into full-depth columns.

`export_regions()` performs a lightweight whole-package orientation selection
at export time. It keeps the semantic source model intact, evaluates the six
bed-facing orientations including a top-down 180-degree flip, computes any
uniform print scale required by the selected profile, and scores the scaled
candidate. Support burden is estimated with a support-volume proxy rather than
only downward face area. Lower support burden and stable bed contact outrank
low print height and scale penalty, so a taller pose may beat a low side-lay
when it materially reduces support material. It applies the selected
rotation/scale/translation to `NAME.3mf` and `NAME.stl`; `NAME-assemble.step`,
`NAME-display.glb`, and semantic region preview meshes stay in semantic object
orientation for CAD and visual review. Repair semantic geometry only for
feature, wall, overlap, coverage, or visual failures; repair bed-fit and height
failures with a different recorded print orientation and uniform print scale
when possible.
Never flatten a full-3D replica, remove underside detail, or alter a handle/head
cross-section solely to clear overhang checks.

## Optical materials

Basic 3MF RGB assignments do not encode filament translucency, transparency,
diffusion, or chemistry. Record optical transmission only when it changes the
model or visual promise, and leave real filament selection to the user and
slicer. Archive color readback proves region RGB assignment, not optical
material behavior.

Never switch profiles or lower limits merely to clear QA. If final dimensions
are explicitly fixed, use a larger profile or real multipart strategy instead
of scaling. Otherwise a recorded uniform print scale is allowed when it
preserves the object better than a worse-support pose. Preserve
identity-bearing geometry, expected feature relationships, semantic color
boundaries, and visual landmarks over eliminating advisory warnings. At most
three repair passes are allowed; unresolved warnings remain visible in the
final status.
