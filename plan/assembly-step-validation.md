# Assembly STEP Validation Plan

This plan tracks the next improvements for physical assembly validation in the
3D generation workflow. The current direction is sound: STEP stays in semantic
CAD coordinates, while STL/3MF serve print placement and package checks. The
remaining gap is turning assembly claims into measurable STEP/B-rep facts.

## Current Baseline

- Multipart intent requires declared parts, connection type, assembly axis,
  clearance, engagement, acceptance text, and modeled connector feature IDs.
- Assembly audit checks report and intent consistency, artifact hashes, part
  ownership, overlap policy, and whether interface feature IDs appear in build
  evidence.
- Color regions are co-printed geometry, not mechanical assembly parts. 3MF
  package and color-region audits are intentionally separate from true
  multipart mechanical assembly.

## Gaps

- The assembly audit mostly proves contract/report consistency; it does not yet
  directly measure mating geometry from STEP.
- Interface feature IDs may exist without proving tab thickness, slot width,
  insertion clearance, engagement depth, or useful contact faces.
- STEP checks validate CAD readability and dimensions, but not the assembly path
  or whether two parts can physically come together along the declared axis.
- Multipart print orientation should eventually be selected per printed part,
  not only for the aggregate assembly.
- Color package audits and mechanical assembly audits need names and schemas
  that make their different meanings obvious.

## Proposed Work

1. Add interface geometry evidence to build reports.
   Each multipart interface should bind the declared feature IDs to measurable
   geometry such as owning part, feature role, local bounding box, mating face or
   slot dimensions, nominal clearance, engagement length, and assembly axis.

2. Add a STEP-driven assembly checker.
   Create `assembly_step_check.py` or extend `step_check.py` so it reads the
   semantic STEP assembly and verifies interface facts with OCCT where possible:
   solid count, per-part bounds, expected feature bounds, clearance windows,
   engagement depth, and obvious inter-part interference.

3. Add simplified insertion-path validation.
   For each interface, sweep or sample one part along `assembly_axis` and check
   that the insertion path is not immediately blocked. Start with conservative
   bounding-box or section checks, then move to B-rep collision checks if the
   simpler evidence is too noisy.

4. Add per-part print orientation for multipart outputs.
   Select print orientation independently for each printed part, because the
   best assembly-level pose is often not the best print pose for every part.
   Preserve a separate semantic assembly STEP for fit review.

5. Separate color package language from mechanical assembly language.
   Keep color-region/package audits focused on co-printed material partitions,
   names, colors, coverage, and 3MF readback. Reserve mechanical assembly audit
   names for multipart parts with real interfaces.

## Success Criteria

- A future run cannot pass a multipart job by merely naming connector features;
  the checker must measure at least the declared clearance and engagement
  evidence from generated geometry.
- STEP evidence explains fit and assembly intent, while STL/3MF evidence
  explains print placement, bed fit, mesh integrity, and package color data.
- The final report can distinguish: "CAD assembly fit evidence passed",
  "static print QA passed", and "actual slicer validation not evaluated".
