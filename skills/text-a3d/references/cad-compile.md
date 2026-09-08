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

The source builds BRep solids, binds their features with `write_scene`, and
exports `<intent.part>_report.json`: use `export_part` for one printed body,
`export_assembly` for separate printed parts, or `export_regions` for volumetric
color regions inside a body. Genuine STEP masters and their derived STL, GLB
and 3MF outputs pass through the same public compile boundary.

The external marker must predate the immutable intent and build source. The
driver creates a separate UUID-bound attempt marker immediately before the
attempt's outputs. After unified build validation, it renders the current
hash-bound GLB for visual diagnosis. It then runs declared installation checks
against the final semantic STEP parts, applicable multipart assembly
checks, per-part and plate STL QA, STEP QA for every genuine STEP, 3MF
color/package QA, and a final freshness audit over this attempt's outputs.
Input files are verified by their report SHA-256 bindings and can retain their
existing timestamps.

The driver has one 5,400-second (90-minute) aggregate compile deadline. Every
source, QA, render, and freshness subprocess receives the smaller of
its stage limit and the aggregate time remaining. Exhaustion fails closed with
`COMPILE.DEADLINE_EXCEEDED` and stops launching further checks. This is a
limit on one compile attempt, separate from the task's runtime timeout. It
does not perform Agent repair or visual review.

## Result semantics

The command prints `a3d-compile-summary/v1` and persists the complete
`evidence-cad-compile-result/v1` at the returned `result.path`. The printed JSON,
including formatting, escaping, and its final newline, is limited to 12,000
characters. It shows at most five error or grouped-warning entries, prioritizing
causes and localization before extra evidence. Nested values, repair hints,
warning identity lists, and delivery metadata also share this budget. Small
measurements are retained ahead of bulky detail. `issueCounts` describes the
recorded findings and existing collection omissions; `diagnostics` separately
reports omitted summary groups and whether projection shortened any detail.
Grouped warnings retain a representative issue's localization and measurements;
`detailScope: representative-issue` labels that sample when a group has multiple
findings, alongside its count and bounded identity lists.
Truncation never changes pass/fail status or the evidence stored on disk.
Evidence paths are kept exact when included. Errors use
stable stage-level codes such as
`CONTRACT.SCENE_INVALID`,
`BACKEND.COMPILE_FAILED`, `BUILD.REPORT_INVALID`, and `QA.MESH_FAILED`.
Checker-specific names remain in `issue.check`; full structured details such as
part/node/interface IDs, component counts, bounds, and observed/expected values
remain in the persisted result. Error messages in the persisted result retain their complete
captured cause; the terminal summary keeps both the beginning and the final
exception when shortening a message. Complete subprocess output stays in
`<name>_compile.log`, while each validator writes its full evidence report to a
unique staged path. A complete validated report is atomically published, so a
deterministic report may be byte-identical to the prior attempt without being
misclassified as stale.

Retrieve more evidence progressively instead of dumping a whole report:

- `a3d diagnose RESULT.json` defaults to errors, up to five per page, with the
  same 12,000-character output budget. `--id ID`, `--code CODE`, and
  `--severity LEVEL` select findings but do not bypass that budget. Use
  `--offset N` with the returned `nextOffset` to continue; `--limit N` can
  request a smaller page. `total`, `count`, `hasMore`, and `projectionTruncated`
  distinguish additional findings from shortened fields on the current page.
- `a3d diagnose RESULT.json --id ID --field message` reads a single top-level
  field in chunks; `actual`, `observed`, and `expected` work the same way.
  `data` contains a fragment of the field's JSON encoding. In this mode,
  `--offset` and `--limit` count UTF-16 code units, with a default chunk size of
  2,000. Follow `nextOffset`; concatenate the chunks and JSON-decode once to
  recover the original field, including Unicode and escaping.
- `--full` explicitly disables output limits for the selected findings or
  field and cannot be combined with pagination. Use it only when the complete
  selection is actually needed. Complete evidence otherwise stays in files.

Checked source operations, interface checks, and applicable artifact QA collect
independent failures into the full result. The driver continues
only while the required upstream artifact remains structurally trustworthy; an
issue with `blockedBy` explicitly identifies a dependency boundary instead of
guessing a downstream diagnosis. The Agent should account for all blocking
findings through bounded pages when the summary omits groups,
group shared causes, make one coordinated source repair, and then rerun.

Every attempt atomically refreshes `<name>_repair-state.json`. Its compact
`failed`, `blocked`, `passedStages`, and `delta` fields preserve factual repair
memory across the same workspace/model revision lineage, including changed
intent filenames and output directories. The returned
`repairDelta` classifies issue identities as `new`, `newlyUnblocked`,
`remaining`, `resolved`, `regressed`, or `scope_changed`. Deleting a requirement
does not prove its geometry repaired, and a source failure leaves unrerun
downstream checks blocked. Repeated root causes are grouped with affected parts
and stages; full occurrences remain in the result. This ledger does not choose actions,
advance states, or impose a retry or wall-clock limit.
Preview images use immutable compile-run filenames. When downstream checks fail
or stop after a valid early render, the result exposes `diagnosticPreview`,
`diagnosticReferencePreview`, and `diagnosticRenderEvidence` for that run.
Use these current-run paths to inspect and improve the current source.
When layout fails before a complete build report, the source may preserve a
diagnostic STEP, GLB and preview. The compiler independently verifies the
candidate's run ID, input bindings and artifact freshness before exposing
`diagnosticStep`, `diagnosticGlb` and `diagnosticPreview`. These artifacts are for
geometry inspection and never count as successful manufacture or final QA.
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
