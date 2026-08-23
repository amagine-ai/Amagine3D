# Construction strategies

Choose a geometry strategy from evidence type instead of forcing every request
through the same primitive stack.

| Evidence | Preferred construction | Avoid |
|---|---|---|
| exact dimensions/drawing | datum-driven solids and explicit cuts | estimating what is already specified |
| clean orthographic silhouette | traced profile, constrained extrusion, then depth features | many hand-placed boxes |
| pixel/icon source | deterministic occupied-cell union or relief | manually copied cells |
| single product photo | primary envelope, landmark solids, then restrained hidden-side inference | claiming unseen details are exact |
| organic/sculptural subject | a small set of lofted profiles or surface-led approximation | hundreds of primitives that create a lumpy silhouette |

## Frame and feature graph

Declare world axes and the reference-facing direction in the intent contract.
Model in dependency order:

1. primary envelope
2. identity-bearing additive volumes
3. functional openings/recesses
4. small controls/details
5. finishes

Give every measured or subtractive feature a stable ID. Call `observe()` before
union and `checked_cut()` for subtraction. Failed operations raise immediately;
do not continue with an unchanged body.

## build123d guardrails

- Primitive alignment is explicit. Put print-facing bottoms at Z=0 unless the
  contract says otherwise.
- Cutting tools extend beyond both target faces to avoid coplanar ambiguity.
- Use transforms as a readable frame chain; do not scatter unexplained signed
  coordinates through the source.
- Select finish edges by semantic geometry or position. `checked_fillet()` and
  `checked_chamfer()` are strict by default; allow reduction only when the
  contract permits it, then report the actual size.
- Preserve symmetry through mirrored geometry or shared parameters.
- Keep source parameters tied to evidence IDs so a repair changes one declared
  cause instead of patching unrelated coordinates.

## Representation checks

`full-3d` needs plausible side/top depth and no facade-only bulk. `relief` and
`orthographic-solid` intentionally prioritize one view but must state thickness.
`surface-led` is appropriate when the recognizable form depends on a controlled
outer surface more than internal mechanics.

If build123d cannot represent the requested organic surface faithfully, stop at
an honest failed visual validation rather than hiding the limitation behind a
watertight STL.
