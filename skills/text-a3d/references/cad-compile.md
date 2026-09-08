# Unified `cad_compile` boundary

`cad_compile.py` is a compiler driver for the existing text-a3d contracts. It
does not prescribe a modeling workflow and never edits intent, scene, source,
or geometry.

## Invocation

Call `a3d compile` with session-relative `marker`, `intent`, `scene`, `source`,
and `output-dir` paths. This is the only normal compilation entry point. The
CLI fixes the workspace to the current session directory and invokes the
managed Python driver without shell interpolation.

The source process receives the text-a3d skill directory at the front of
`PYTHONPATH`, so generated source can import `cad_helpers` directly. The driver
also recomputes every returned artifact SHA-256.

The active intent must already exist and is validated before any
Agent-authored geometry executes. Create it in a separate contract-only
authoring step; never call `write_intent(...)` from the build source or run the
full build source manually to bootstrap it. After intent validation, the
declared build source runs and may generate or refresh only the scene and its
geometry inputs. The driver then verifies that the intent hash did not change
and validates the resulting scene.

- An all-BRep scene requires the source to export `<intent.part>_report.json`:
  use `export_part` for one printed body, `export_assembly` for separate printed
  parts, or the color exporter for volumetric regions inside a body.
- A scene containing any mesh-master part selects `hybrid_compile.py`. For a
  mixed scene, the source binds ordinary Mesh and BRep feature geometry directly
  from the authored objects. Only final BRep-master parts also bind genuine
  STEP; BRep features fused into a mesh-master part do not. Finish source with
  `write_scene`; the driver performs the final Hybrid export.

The external marker must predate the immutable intent and build source. The
driver creates a separate UUID-bound attempt marker immediately before the
attempt's outputs. After unified build validation, it renders the current
hash-bound GLB for visual diagnosis. It then runs applicable multipart assembly
checks, per-part and plate STL QA, STEP QA for every genuine STEP, 3MF
color/package QA, and a final freshness audit over this attempt's outputs.
Input files are verified by their report SHA-256 bindings and can retain their
existing timestamps.

The driver has one 5,400-second (90-minute) aggregate compile deadline. Every
source, backend, QA, render, and freshness subprocess receives the smaller of
its stage limit and the aggregate time remaining. Exhaustion fails closed with
`COMPILE.DEADLINE_EXCEEDED` and stops launching further checks. This is a
limit on one compile attempt, separate from the task's runtime timeout. It
does not perform Agent repair or visual review.

## Result semantics

The command prints `a3d-compile-summary/v1` and persists the complete
`evidence-cad-compile-result/v1` at the returned `result.path`. The printed view
shows actionable errors and omitted counts, groups repeated warnings, projects
evidence to useful paths, and directly lists manufacturing deliverables,
physical parts, and compiled colors when available. Open the persisted JSON only when the
summary is insufficient; prefer `a3d diagnose RESULT.json --id ID` (or a code
or severity selector) so unrelated evidence does not enter context. Errors use
stable stage-level codes such as
`CONTRACT.SCENE_INVALID`,
`BACKEND.COMPILE_FAILED`, `BUILD.REPORT_INVALID`, and `QA.MESH_FAILED`.
Checker-specific names remain in `issue.check`; full structured details such as
part/node/interface IDs, component counts, bounds, and observed/expected values
remain in the persisted result. Complete subprocess output stays in
`<name>_compile.log`, while each validator writes its full evidence report to a
unique staged path. A complete validated report is atomically published, so a
deterministic report may be byte-identical to the prior attempt without being
misclassified as stale.

Checked source operations, Hybrid part/cutter/overlap checks, and applicable
artifact QA collect independent failures into the full result. The driver continues
only while the required upstream artifact remains structurally trustworthy; an
issue with `blockedBy` explicitly identifies a dependency boundary instead of
guessing a downstream diagnosis. The Agent should review the whole issue set,
group shared causes, make one coordinated source repair, and then rerun.

Every attempt atomically refreshes `<name>_repair-state.json`. Its compact
`failed`, `blocked`, `passedStages`, and `delta` fields preserve factual repair
memory across attempts sharing the same immutable intent. The returned
`repairDelta` classifies issue identities as `new`, `newlyUnblocked`,
`remaining`, `resolved`, or `regressed`. This ledger does not choose actions,
advance states, or impose a retry or wall-clock limit.
Preview images use immutable compile-run filenames. When downstream checks fail
or stop after a valid early render, the result exposes `diagnosticPreview`,
`diagnosticReferencePreview`, and `diagnosticRenderEvidence` for that run.
Use these current-run paths to inspect and improve the current source.
After all automated checks and freshness pass, the compiler rechecks the
source/report/artifact bindings and atomically publishes `<name>_render.json`.
That successful result exposes `preview`, `referencePreview`, and
`renderEvidence`. A failed attempt preserves the previous successful render
pointer, so read the image named by the latest compile result rather than a
filename glob or that older pointer. Freshness evidence is accepted only when it
names this attempt marker and exactly covers every requested artifact with
fresh, existing files whose mtime, byte size, and SHA-256 still exactly match
the checker evidence. The canonical compile log is excluded from this set
because the freshness subprocess writes its own stdout into that log; its final
hash is instead recorded in the compile result after the subprocess exits.
Unexpected untyped compiler failures use `INTERNAL.COMPILER_ERROR` instead of
guessing a geometry diagnosis from traceback wording.

`pass: true` means the automated compile, QA, and render steps passed. The
status is still `awaiting-visual-review` and `deliveryReady` remains false.
Read the returned preview with native `view_image`, compare it with the user's
request, and improve the source where useful. Communicate the actual visual
result and remaining limitations in the response. Visual review has no separate
post-read delivery API and does not change those compiler fields.

## Integration constraints

- Invoke the driver through `a3d compile`; do not recreate it as a server-side
  state machine or automatic repair loop.
- Keep every supplied path in the current session workspace and use the managed
  `.venv` Python selected by the CLI.
- Read full logs or issue bodies from persisted evidence only when the concise
  command result is not enough to diagnose a failure.
- Do not add automatic repair, geometry simplification, scaling, relaxed QA
  thresholds, motion semantics, or motion QA to this boundary.
- Codex `workspace-write` is the operating-system write boundary. The CLI path
  checks are defense in depth, not a separate hostile-code sandbox.
