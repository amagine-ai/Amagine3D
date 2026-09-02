# Unified `cad_compile` boundary

`cad_compile.py` is a compiler driver for the existing text-a3d contracts. It
does not prescribe a modeling workflow and never edits intent, scene, source,
or geometry.

## Invocation

Call the Agent's `cad_compile` tool with session-relative `marker`, `intent`,
`scene`, `source`, and `output_dir` paths. This is the only normal compilation
entry point. The runtime resolves those paths inside the session workspace and
invokes the managed Python driver without a shell.

The source process receives the text-a3d skill directory at the front of
`PYTHONPATH`, so generated source can import `cad_helpers` directly. The tool
also recomputes every returned artifact SHA-256 before exposing the compact
result.

The immutable intent must already exist and is validated before any
Agent-authored geometry executes. Create it in a separate contract-only
authoring step; never call `write_intent(...)` from the build source or run the
full build source manually to bootstrap it. After intent validation, the
declared build source runs and may generate or refresh only the scene and its
geometry inputs. The driver then verifies that the intent hash did not change
and validates the resulting scene.

- An all-BRep scene requires the source to export `<intent.part>_report.json`
  through the existing `cad_helpers`/color exporter contract.
- A scene containing any mesh-master part selects `hybrid_compile.py`. For a
  mixed scene, the source must first generate and bind every required genuine
  BRep master STEP plus mesh input.

The external marker must predate the immutable intent and build source. The
driver creates a separate UUID-bound attempt marker immediately before the
attempt's outputs. It then runs unified build validation, generic multipart
interface/assembly geometry checks when applicable, per-part and plate STL QA,
STEP QA for every genuine STEP, 3MF color/package QA when applicable, a fresh
hash-bound GLB render, and one final freshness audit over outputs of that
attempt. Input files are verified by their report SHA-256 bindings rather than
being required to receive new timestamps.

The driver has one 5,400-second (90-minute) aggregate compile deadline. Every
source, backend, QA, render, and freshness subprocess receives the smaller of
its stage limit and the aggregate time remaining. Exhaustion fails closed with
`COMPILE.DEADLINE_EXCEEDED` and stops launching further checks. This is a
fail-fast boundary that leaves roughly 30 minutes in a two-hour task for Agent
repair and visual review; it is not a performance optimization, repair loop,
or server-side workflow state.

## Result semantics

The command prints and persists `evidence-cad-compile-result/v1`. Errors use
stable stage-level codes such as `CONTRACT.SCENE_INVALID`,
`BACKEND.COMPILE_FAILED`, `BUILD.REPORT_INVALID`, and `QA.MESH_FAILED`.
Checker-specific names remain in `issue.check`; structured details such as
part/node/interface IDs, component counts, bounds, and observed/expected values
are returned together when available. Complete subprocess output stays in
`<name>_compile.log`, while each validator writes its full evidence report to a
unique staged path. A complete validated report is atomically published, so a
deterministic report may be byte-identical to the prior attempt without being
misclassified as stale.

Checked source operations, Hybrid part/cutter/overlap checks, and applicable
artifact QA collect independent failures into one result. The driver continues
only while the required upstream artifact remains structurally trustworthy; an
issue with `blockedBy` explicitly identifies a dependency boundary instead of
guessing a downstream diagnosis. The Agent should review the whole issue set,
group shared causes, make one coordinated source repair, and then rerun.

Every attempt atomically refreshes `<name>_repair-state.json`. Its compact
`failed`, `blocked`, `passedStages`, and `delta` fields preserve factual repair
memory across attempts sharing the same immutable intent. The returned
`repairDelta` classifies issue identities as `new`, `newlyUnblocked`,
`remaining`, `resolved`, or `regressed`. This ledger does not choose actions,
advance states, or split work between Agents.
Preview images use immutable compile-run filenames. The canonical render
evidence is published atomically only after both images exist and are
hash-bound; interrupted publication removes this run's orphan images and
preserves the previous evidence. Freshness evidence is accepted only when it
names this attempt marker and exactly covers every requested artifact with
fresh, existing files whose mtime, byte size, and SHA-256 still exactly match
the checker evidence. The canonical compile log is excluded from this set
because the freshness subprocess writes its own stdout into that log; its final
hash is instead recorded in the compile result after the subprocess exits.
Unexpected untyped compiler failures use `INTERNAL.COMPILER_ERROR` instead of
guessing a geometry diagnosis from traceback wording.

`pass: true` means the automated compile, QA, and render steps passed. The
status is still `awaiting-visual-review`, `deliveryReady` remains false, and the
Agent must read the new preview before the existing final freshness/delivery
gate. The compile-time freshness artifact proves that automated inputs and
outputs belong to this run; the post-read delivery gate separately proves that
the Agent actually inspected them. This keeps visual judgment with the Agent
rather than pretending a Python process can perform image review.

## Integration constraints

- Register the driver as one PI tool, not a server-side state machine.
- Register `cad_capabilities` and `reference_analyze` as optional peer tools in
  the same Agent loop, not mandatory preprocessing states.
- Resolve every supplied path under the current session workspace before
  spawning it, use the managed `.venv` Python, and propagate `AbortSignal` to
  the complete child process group.
- Surface stage activity without returning the full compiler log to model
  context. The compact result is intended for the next Agent decision.
- Do not add automatic repair, geometry simplification, scaling, relaxed QA
  thresholds, motion semantics, or motion QA to this boundary.
- The Python path checks and argv-only spawn reduce accidental misuse but are
  not an operating-system sandbox. Agent-authored Python still inherits the
  server process permissions.
