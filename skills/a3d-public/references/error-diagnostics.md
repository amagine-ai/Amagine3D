# Error diagnostics

Use this page to choose evidence and the next check. The API contract stays in
[cad-agent-api.md](cad-agent-api.md), compiler result semantics in
[cad-compile.md](cad-compile.md), and local DSL reads/edits in
[parametric-design.md](parametric-design.md). Load operation details only when
needed: [booleans](operations/booleans.md) and [finishing](operations/finishing.md).
Other routes: [surface and shell](surface-shell.md),
[installation](installation-checks.md), and [printability](bambu-printability.md).

## Triage

1. Read the returned result. `status=api-error` may contain structured
   `error.type`/`error.message`, request details and JSON line/column. A large
   error can instead be a string plus a bound `report`; inspect `/error` with
   that report's SHA. It may have no issue ID or CAD report. Fix the request
   first.
2. For a current failure, use its exact report `path` and `sha256`. On recovery,
   run `a3d agent context` and use `lastFailure`, `inputsCurrent`, `nextInspect`
   and `nextDesignRead`. Historical failures help compare behavior only; they
   do not accept current inputs.
3. Read the returned issue and its actual fields: `code`, `check`, `ruleId`,
   `stage`, issue ID, part/feature/node, `field`, `observed` or `actual`,
   `expected`, `repairHint`, `blockedBy`, and available witnesses. `field` can
   locate the source declaration; use `evidenceField` or the returned JSON
   pointer to read report evidence. Do not infer missing facts.
4. Inspect large or omitted evidence through the exact report binding. Preserve
   `sameAs` references, resolve large-field references, and concatenate ordered
   `textChunk` values using their offsets. Continue with the complete
   `page.nextRequest`; a changed selector/report needs a fresh query.

Summary pages paginate issues, so later pages may contain additional findings.
Large fields are replaced with resolvable references; follow those references
to inspect the complete saved evidence.

```bash
 a3d agent inspect '{"report":"RESULT.json","sha256":"REPORT_SHA256","issueId":"ISSUE_ID","field":"/observed"}'
 a3d agent inspect --args-file next-inspect.json
```

The args file contains the returned `page.nextRequest`. If no unique issue ID
exists, inspect a returned field pointer. Legacy `a3d diagnose RESULT.json
--id ISSUE_ID --field observed` remains supported; its offset/limit pagination
is separate from Agent API cursors. Do not require an issue lookup for
`api-error`.

For `AUTHORING.INVALID`, `DESIGN.BINDING_MISSING` and `DESIGN.OWNERSHIP`, read
`ruleId`, declaration `field`, owner/part, feature or node, and `actual` /
`expected`. Correct the declaration, binding or ownership at that location;
do not delete requirements, invent geometry or relax a target to clear the
issue. Revalidate before building. For `DESIGN.REVISION_CONFLICT`, follow the
current-revision procedure below.

For revision conflicts, use the returned current revision, then read affected
nodes and apply a local edit against that revision; never overwrite concurrent
changes. For stale report SHA, cursor, or old failures, reacquire current
context/report and start the query again. On timeout or unknown failure, use
context's running attempt/log path, identify the failed stage, retain inputs
and complete logs, and record what evidence is absent. Do not assign an
unreported kernel cause.

## Recovery and result boundaries

Follow context's exact `nextInspect` and `nextDesignRead`. `inputsCurrent=false`
means saved evidence is stale. `lastFailure` remains relevant until its bound
checks pass; a passing static validation or another design's draft cannot clear
it. Context reports `lastFailure.inputsCurrent`; false makes that failure
historical for the active inputs. `blockedBy` means a check is blocked by an
upstream failure; it is unresolved and never a pass.

`cadExport=passed` proves STEP readback against the BRep; it does not mean
manufacturing passed. A draft or ordinary display success cannot override a
manufacturing failure. When `status=awaiting-preview` and `pass=false`, use the
exact `nextDisplay` from current context. Display retry may reuse passed
manufacturing evidence only after report, input, intent lineage, capability and
artifact bindings verify. It can then reach `awaiting-visual-review`; inspect
the actual preview before claiming it was reviewed. A returned image path or
checkpoint is not proof of viewing. See [the API contract](cad-agent-api.md).

Treat every cause as a hypothesis until its evidence supports it. Keep frozen
intent, targets and tolerances fixed. A failed operation that returns its prior
valid geometry is still a failure. The available exception text and witnesses
are not a complete OCCT trace.

## Applicability and reproduced cases

These guides were checked with build123d 0.11.1 and the installed
`cadquery-ocp-novtk` / `cadquery-ocp-proxy` 7.9.3.1.1 distributions. The
reproducible regression scope includes a missed cut in an existing empty space,
detached and touching union components, Python empty-edge selection and failed
finishing attempts (including original-shape return and report-SHA `inspect`),
DSL selector count/kernel failure, malformed API request location, stale
revision rejection, and manufacturing pass retained across preview retry.
These tests validate the listed evidence and recovery boundaries; they do not
claim exhaustive OCCT diagnostics.

Maintainers can reproduce the focused regressions from the repository root with
managed Python. CAD session workspaces do not necessarily contain repository
scripts or tests; ordinary task diagnosis should use the public `a3d` CLI above.

```bash
TMPDIR="$PWD" PYTHONDONTWRITEBYTECODE=1 node scripts/run-project-python.mjs -m unittest \
  tests.python.test_skill_shared_files \
  tests.python.test_source_diagnostics \
  tests.python.test_cad_diagnostics \
  tests.python.test_build_session.BuildSessionTests.test_failed_finish_preserves_geometry_and_diagnostic_in_compile_mode \
  tests.python.test_agent_api.AgentOperationsTests.test_real_finishing_failure_inspect_uses_report_sha_and_preserves_report_bytes \
  tests.python.test_design_language.DesignGeometryTests.test_selector_count_and_kernel_failure_keep_the_requested_radius \
  tests.python.test_agent_api.AgentOperationsTests.test_malformed_request_file_returns_json_location \
  tests.python.test_design_language.DesignEditTests.test_stale_revision_and_external_edit_do_not_overwrite \
  tests.python.test_agent_api.AgentOperationsTests.test_manufacturing_pass_survives_render_failure_and_preview_retry_reuses_checks \
  tests.python.test_agent_api.AgentOperationsTests.test_validate_and_display_success_preserve_current_manufacturing_failure \
  tests.python.test_agent_api.AgentOperationsTests.test_another_designs_draft_and_failure_cannot_route_current_recovery
```
