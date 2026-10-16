# Boolean diagnostics

Use the exact report/SHA and issue pointer described in
[Error diagnostics](../error-diagnostics.md). A concrete source issue and a DSL
wrapper issue can describe the same operation from different layers; preserve
both identities, node/field locations and evidence. A wrapper code alone does
not establish the kernel cause. `operandInsideOwnerBounds` reports only
axis-aligned box containment; it cannot establish an enclosed cavity or explain
prior cut history.

## Cut missed its owner

- **Symptom:** `SOURCE.CUT_MISSED_OWNER`; DSL may also report a node-level
  `DESIGN.KERNEL_FAILED` or postcondition issue.
- **Facts:** source evidence can include `featureId`, `partId`,
  `observed.removedMm3`, tool statistics and `observed.booleanWitness`;
  `expected.minimumRemovedMm3` is the required removal. DSL diagnostics retain
  `nodeId`/`field` and available `designEvidence`.
- **Hypotheses:** cutter is in a cavity, misses the owner, has the wrong frame,
  or has a profile/depth that does not cross material. Zero removal alone does
  not distinguish them.
- **Read:** removal threshold, body/tool bounds and volume, witness components,
  coordinates and measurement errors when present; for DSL read the node and
  referenced frame/profile/depth using `parametric-design.md`.
- **Verify next:** compare cutter and owner in the same frame; use witness
  overlap/intersection details if present, then inspect the resulting feature.
- **Allowed repair:** correct owner binding, frame, profile or depth while
  preserving target dimensions and clearance. Do not weaken the minimum-removal
  contract to hide a miss.
- **After repair:** rebuild, inspect the new bound report, confirm removed
  volume meets `expected`, and rerun downstream checks.
- **Limit:** a witness may be partial or record measurement error; it is not a
  full kernel trace. A DSL wrapper does not prove why the source operation
  failed.

## Union disconnected

- **Symptom:** `SOURCE.UNION_DISCONNECTED`; DSL may provide a node-level wrapper.
- **Facts:** source evidence can include `observed.result.bodyCount`,
  addition/body stats and `observed.booleanWitness.components`, including
  connectivity, gap and nearest-point measurements when computable. Raw geometry
  stats may call the solid count `solid_count`. DSL adds node/field and
  `designEvidence` where available.
- **Hypotheses:** addition is separated, touches only at a point/edge, or is
  bound to the wrong owner. A component count does not identify which. Zero gap
  or touching does not guarantee one connected solid.
- **Read:** `bodyCount`, witness per component, gap/nearest-point fields,
  body/addition bounds, owner and node bindings.
- **Verify next:** inspect the reported components and positions relative to the
  intended owner; confirm physical connection has positive material overlap
  where required.
- **Allowed repair:** correct placement, owner or join geometry within fixed
  design requirements. Do not add arbitrary material solely to force one solid.
- **After repair:** rebuild and confirm one valid connected solid plus intended
  feature dimensions and downstream QA.
- **Limit:** missing or unresolved witness measurements leave connectivity
  cause unknown; do not present a guess as an OCCT diagnosis.

## Boolean execution failed

- **Symptom:** `SOURCE.CHECKED_CUT_FAILED` or
  `SOURCE.CHECKED_UNION_FAILED`; a DSL operation may wrap it as
  `DESIGN.KERNEL_FAILED`.
- **Facts:** `observed` may contain body/tool or body/addition statistics and
  captured `error`; `expected` gives the operation threshold. Blocked downstream
  issues can carry `blockedBy`.
- **Hypotheses:** invalid or difficult input topology, operation interaction,
  or another cause. Generic exception text does not choose among these.
- **Read:** original source issue, input validity/statistics, stack/log and any
  earlier concrete diagnostic; for DSL read node, `op`, `field` and available
  `designEvidence`.
- **Verify next:** validate inputs independently, then isolate the operation
  with the same bound inputs; compare a specific witness if available.
- **Allowed repair:** correct demonstrated input, owner or feature construction
  issues; preserve requested design and report remaining uncertainty.
- **After repair:** rerun the operation and full build/validation against current
  inputs, then inspect all unblocked issues.
- **Limit:** public evidence is not a complete OCCT trace. A failed source
  operation may return the prior body for diagnostic continuity; that is not
  successful geometry.
