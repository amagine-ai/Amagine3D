# Fillet and chamfer diagnostics

Use [Error diagnostics](../error-diagnostics.md) for report binding and
[parametric design](../parametric-design.md) for DSL node reads. Keep source
and DSL diagnostics separate when both are present.

## Python selector found no edges

- **Symptom:** `SOURCE.CHECKED_FILLET_FAILED` or
  `SOURCE.CHECKED_CHAMFER_FAILED`.
- **Facts:** `observed.selectedEdgeCount=0`; `expected.selectedEdgeCount` is
  `>=1`. This code also covers execution failure, so inspect observed fields.
- **Hypotheses:** selector predicate, topology or operation order no longer
  matches the current shape.
- **Read:** issue `featureId`, source `field` if present, selector definition,
  current shape and selection count.
- **Verify next:** enumerate candidates and check the predicate against measured
  curve, center, length/radius or direction.
- **Allowed repair:** correct selector or prior construction while preserving
  requested finishing dimensions and frozen requirements.
- **After repair:** rebuild and confirm positive selection count, intended
  edges, requested size and downstream geometry checks.
- **Limit:** zero identifies selection failure, not which predicate is wrong.
  Do not change target radius/length to make selection pass.

## Python finishing execution failed

- **Symptom:** same `SOURCE.CHECKED_FILLET_FAILED` or
  `SOURCE.CHECKED_CHAMFER_FAILED`, with nonzero selection.
- **Facts:** `observed.attempts` contains failed requested/reduced-size attempts;
  `expected.requestedMm` records requested size. Attempts distinguish this from
  empty selection.
- **Hypotheses:** selected edges interact with local geometry in a way that
  prevents a valid result. Error text alone does not prove the cause.
- **Read:** requested size, attempts, selected edge identity/predicate, local
  topology and any specific validity diagnostic.
- **Verify next:** isolate the selected edge group on the same input and inspect
  result validity and affected region.
- **Allowed repair:** correct a demonstrated selector or construction issue.
  Parameter exploration is allowed only within unchanged intent and acceptance
  bounds; reduction is not a diagnosis or permission to relax a target.
- **After repair:** confirm output differs as intended, remains valid, and
  passes unchanged dimension, interface and manufacturing checks.
- **Limit:** attempts are captured exceptions, not a complete OCCT trace. In
  diagnostic collection mode failure can return the original shape; it remains
  failed and cannot be reported as successful finishing.

## DSL selection or finishing failure

- **Symptom:** `DESIGN.SELECTION_EMPTY`, `DESIGN.SELECTION_COUNT`,
  `DESIGN.KERNEL_FAILED` or `DESIGN.POSTCONDITION_FAILED`.
- **Facts:** selection errors provide `actual.count`, `expected`, `nodeId` and
  `field`; inspect `designEvidence` for candidate count and centers. Kernel and
  postcondition wrappers identify a node/op but do not identify the underlying
  source cause by themselves. DSL finishing keeps the declared radius/length;
  it does not reduce them automatically (`allow_reduce=False` behavior).
- **Hypotheses:** `where` predicates no longer match, expected count is wrong
  for the intended edge set, or the operation fails/returns an unacceptable
  result.
- **Read:** node, `op`, `field`, `edges.where`, `edges.of`, `expect.count`, actual
  count, candidate centers and any concrete source issue.
- **Verify next:** read current node and referenced upstream nodes at returned
  revision; compare candidates with intended edges and operation inputs.
- **Allowed repair:** update selector or upstream construction only when
  evidence supports it. Keep `expect.count` aligned with intent; do not change it
  merely to silence the issue.
- **After repair:** apply a local edit against current revision, rebuild, and
  verify selection, resulting geometry and unchanged acceptance checks.
- **Limit:** candidates/centers are evidence, not proof a candidate is the
  intended edge. Do not infer a kernel root cause from a DSL wrapper alone.
