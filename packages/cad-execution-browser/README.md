# @amagine3d/cad-execution-browser

> **简体中文**：[查看中文版](./README.zh-CN.md)

Framework-free browser CAD execution for Amagine3D. The public entry point
provides a recoverable `BrowserCadExecutor`; the implementation runs only in a
dedicated Web Worker and follows this path:

```text
BrowserCadExecutor -> versioned Worker protocol -> Pyodide -> OCP.wasm
                   -> build123d -> Amagine3D host operations -> mesh audit
```

The executor bootstraps lazily. A timeout, abort, invalid response, or Worker
crash terminates that Worker; the next request creates and bootstraps a new
one. Progress and bounded logs are events. Binary artifacts are returned as
transferable `ArrayBuffer` values with SHA-256 metadata.

## Runtime contract

- Runtime versions and SHA-256 values live in `runtime-assets.json` and are
  exposed through `runtime-manifest.ts`.
- Preparation concatenates the exact Pyodide core/package closure, pinned
  project wheels and Amagine3D workflow runtime assets into one deterministic
  same-origin bundle. The Worker verifies its pinned SHA-256 and strict index
  before loading Pyodide from memory; unexpected fragmented runtime requests
  are rejected.
- The exact build123d, OCP.wasm, lib3mf, and trimesh wheel filenames and
  SHA-256 values are pinned. Preparation verifies each downloaded asset and
  bootstrap verifies the complete bundle before the trusted installer runs.
- The Worker mounts the dedicated `amagine3d-cad-worker` OPFS directory at
  `/opfs`, isolated from project transactions written by the main thread. A
  build uses `/<project-id>/.cad-worker/<run-id>` and removes that transient
  directory after the transferable artifacts have been assembled.
- Only one build runs per Worker. Main-thread cancellation and timeout use
  Worker termination because a busy Python interpreter cannot reliably
  consume a second cancel message.

## Source boundary

Source is checked once on the host and again with Python `ast` in the Worker.
The runtime permits public build123d imports, `math`, and the five public helper
operations for the frozen workflow profile. It rejects direct file access,
dynamic evaluation, dunder traversal, direct exporters, network/process
modules, cross-profile helpers, and output directories outside `cad_out`.

The executed namespace receives restricted builtins and a restricted
`__import__`. Trusted build123d and helper modules bootstrap outside that
namespace. The Worker contains no model key, search service, cookies, or
provider SDK.

## QA and persistence

Single-color runs perform shape validity, missed-cut, watertight, winding,
positive-volume, component-count, dimensions, and optional volume checks.
Multi-color runs add exact region-plan matching, per-region mesh QA, pairwise
overlap, and an actual `amagine_three_mf.inspect_3mf` readback. Best-effort
colored STEP is a warning; missing or unreadable 3MF and region overlap are
failures.

When the frozen design brief declares a rigid mechanism, both profiles also
reopen every published body or region STEP, require the declared moving and
stationary IDs to partition the publication exactly, and sample every ordered
rotation, translation, or screw motion. Any unknown geometry result, swept
collision above tolerance, or declared running clearance below target fails QA.
Failure diagnostics also identify the worst colliding body pair and motion
sample plus the poses at which minimum and maximum clearance occur, so the
Agent can repair the responsible interface instead of redesigning blindly.

Frozen feature checks read bounds, centres, and volume from matching
`observe_feature` records. Observations tagged `keep-out` are intersected with
every final published body or region; missing measurements, unknown
intersections, and keep-out overlap above 0.01 mm³ fail QA.

The `publish_color_model` dictionary must use each frozen color region's `id`
as its key. Human-readable region `name` values are labels for the UI and may
contain spaces; they are not part of the Python export contract.

The storage package's `persistSuccessfulExecution` consumes the shared
`CadExecutionResult`, refuses failed QA, saves durable artifacts through the P2
`ProjectRepository`, marks the run immutable, and advances the project's
current-run pointer. Every verified STEP is persisted as a primary project
artifact, so reopening a project or exporting its ZIP preserves the exact B-rep
that passed QA. Keeping that adapter in storage preserves the package dependency
boundary.

The package does not import React; the web workbench accesses it through the
`BrowserCadExecutor` boundary.
