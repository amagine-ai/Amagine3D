# Bambu printability for color assemblies

Resolve this skill's printer profile before geometry. The profile fixes the
machine, selected tool, nozzle, standard process, printable polygon, line-width
floor, wall target, and support threshold for the entire evidence run.

## Audit the manufacturing mesh

Color-region meshes are semantic partitions, not independent evidence of how a
co-printed object is supported. A downward face in one region may sit directly
on another material. For that reason:

1. Audit each region with `--topology-only` to prove its mesh and components.
2. Pass `parent=` for every co-printed body so `export_regions()` can prove
   coverage and export a clean `NAME-manufacturing.stl`.
3. Run profile-backed bed, feature, wall, and overhang checks on that
   manufacturing STL only.

If regions are separately manufactured parts, omit `parent=` only when the
intent records that architecture. The manufacturing STL may then have multiple
components; pass the reported `manufacturing.solid_count` to `--components` and
review each part's actual print orientation separately. `NAME.3mf` is the
colored print package for the slicer. `NAME-region-REGION.stl` files prove
region topology; they are not substitutes for manufacturing printability QA.

## Design targets

- Keep every normal feature at or above `single_line_floor_mm`.
- Use `process_wall_target_mm` for shells, internal walls, dividers, and region
  interface walls. Meeting it proves slicer compatibility, not strength.
- Put co-printed material interfaces on the build plane or on already printed
  material where possible.
- Avoid thin decorative color skins that are below one practical layer or one
  extrusion width.
- Treat purge reduction as secondary to appearance, region integrity, and
  printable boundaries.
- For internal paths, sockets, fasteners, and installed components, observe a
  representative local clearance feature. Do not use the global bounding box
  of a bent or compound cut tool as proof of its narrowest section.

## Supports and orientation

The profile's support angle is measured upward from horizontal. Prefer a
support-free orientation for the manufacturing assembly. Reorient, slope, chamfer,
arch, or split the object before declaring supports required. Do not treat a
bridge as automatically safe, and do not infer support need from an isolated
co-printed region.

## Optical materials

Basic 3MF RGB assignments do not encode filament translucency, transparency,
diffusion, or chemistry. Record optical transmission in the intent and deliver
the generated material plan. Any non-opaque region requires an explicit slicer
filament assignment; archive color readback alone is not an optical-material
pass.

Never switch profiles, lower limits, or scale fixed user dimensions merely to
clear QA. At most three repair passes are allowed; unresolved warnings remain
visible in the final status.
