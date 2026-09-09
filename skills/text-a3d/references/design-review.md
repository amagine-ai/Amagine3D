# Design reasoning and review

Use this reference when appearance, an assembly, a moving part, an installed
component, or failed QA needs design judgment. Scale the depth of review to the task; these are
questions to resolve, not a prescribed sequence or a requirement to add mechanisms.

## Preserve the purpose while improving the construction

Identify what each connection accomplishes: location, load transfer, retention,
motion, disassembly, or service access. An alternative construction is useful when
it retains the required behavior. A locating pin still needs its mating fit even
if reclassified as detail; adhesive is an intentional assembly choice, not an
exception to select because it has fewer checks.

Distinguish source-backed requirements from initial engineering assumptions before
writing intent. Give ordinary exterior dimensions a nominal value and the initial
±0.1 mm range described in the main skill; retain explicit user tolerances and
resolve functional fits separately. Correct mistaken assumptions using new evidence and explain their
effect on function; do not overwrite a valid target simply to match the current
artifact. If the immutable contract cannot express a necessary change, state the
conflict and proposed replacement against the original request rather than
silently deleting it. Do not freeze an arbitrary first idea as a user requirement.

## Datums, assembly, and operation

Derive related faces from one dimension chain: outside envelope, wall or floor,
inside clearance, component stack, and mating plane. Place matching features from
one datum. Check cutter direction against the two surfaces it must cross, and
measure the final envelope rather than treating a local extrusion height as the
assembled height. Repeated interference often points to one shared datum error.

Describe how separately manufactured parts enter their final positions. A final
non-intersecting pose does not establish an assembly path. For a captive mechanism,
consider how the captive member passes its guide before retention is completed;
choose a split, assembly feature, or justified material deformation that makes the
path feasible. Do not invent elasticity for an ordinary rigid printed part.

For requested motion, relate rest and operated positions, axis sign, guide overlap,
stops, travel clearance, contact with the driven component, and return behavior.
Check the travel interval or relevant swept geometry, not just the resting pose.
Recipe travel metadata does not create a stop, spring, or contact surface. Show the
chosen return element's installed envelope and seating when operation depends on
it, even if that element is purchased and excluded from print artifacts.

## Installed components

For a component whose installation belongs in the request, use a parameterized
envelope and installation datum to develop the needed clearance, support,
retention and access. Relate its supported position to the surfaces that carry
the load. A visual concept can keep unspecified hardware as proposed dimensions
and focus on its modeled relationship to the enclosure.

Resolve the installed condition as well as entry: what supports the component,
what prevents unintended movement, and what closure or protection its use needs.
Check these relationships on the first functional construction, before adding
detail that depends on them. For final compilation, declare applicable
`installation_checks` on the receiving intent feature and bind witness geometry using
`installation-checks.md`; a reference node or an empty cavity alone proves none
of these relationships. Choose requirements by function, without a universal
fastener, cover or sealed-bottom rule.

Keep purchased items out of manufactured part counts. `installed-displays.md`
shows how to bind a visible component to its actual physical aperture, seat or
cavity and how to extend that construction for an installed module.

## Interpret evidence at the scale it measures

Observe the actual mating subfeature before combining it with larger bodies. The
diameter of a complete knob does not measure its shaft. Use measurable endpoint
fields such as `diameter`, `width`, `depth`, or `length` for the corresponding
observed geometry; renaming a field without fixing the feature binding is not proof.
Use the returned recipe cutters and solids, or accurately bind the geometry you
constructed. Do not label independently built geometry as an unconsumed recipe.

An `EVIDENCE_NOT_EVALUATED` warning means unknown, not passed. Resolve missing proof
for required function; if a check does not apply, explain why using the geometry.
Assess print warnings at their locations: residual wall around an opening,
supported starting layers, overhangs, and load-bearing sections. Choose each part's
print orientation for those features while preserving its semantic assembly pose.

If packing fails, distinguish a part exceeding the machine envelope from a
heuristic failing to arrange otherwise printable parts. Explore placement or an
explicit manufacturing/package alternative supported by the tools. Do not resize
the product or weaken QA merely to satisfy the current packing implementation.

## Visual review

Inspect uploaded images directly as the primary visual reference. For appearance-led
work without one, use available native search to find and actually view a small
relevant set when network access is enabled. Record URLs and useful silhouette,
proportion or surface relationships in the workspace, separating observations from
interpretation. Dimension-driven parts need no unrelated image search.

Follow runtime network instructions. `CODEX_WEB_SEARCH_ENABLED` defaults to true;
false disables search. Enabled configuration does not guarantee provider tools or
image perception. Use available tools directly, without per-task capability probes;
if a step fails, identify missing evidence and continue with supplied/local evidence.
A page title is not visual inspection. For deterministic palette/silhouette facts,
use `a3d reference IMAGE --out REPORT.json`. Perspective appearance is approximate;
do not claim exact reproduction without measurements.

For uncertain fit or mounting dimensions, use primary component drawings or supplier
specifications when network access is enabled. Carry exact component identity and
source into intent; a similar product is not an exact specification. Otherwise keep
proposed envelopes and dimensions as reversible parameters.

Extract the relationships that shape the result: relative volume, silhouette,
surface transitions, spacing and integration of secondary forms.

For a new model with unresolved construction, `a3d draft <name>_build.py` previews
the same BuildSession source before intent/feature registration when it declares
part names. For an existing contract-bound source, supply `--intent INTENT.json`.
Draft artifacts are provisional; retain the chosen geometry and declare the
complete functional requirements for final `a3d compile`.

Inspect the primary-form preview as soon as it can be rendered. Compare it with
those relationships and the brief, then change the responsible source parameters
before adding dependent detail. A list of present components does not establish
that the form matches. Keep visual discrepancies visible through engineering
repairs; a successful compile does not resolve them.

The standard preview has five views: isometric, front, side, top, and bottom.
Use them for silhouette, count, proportions, spacing, seams, and exposed openings.
Read the current render named in the compile result, not an older matching glob.
Use available views of that same geometry to understand occluded or localized
features. When the current tools support a focused, section or exploded view,
use it as supplemental evidence while retaining the canonical assembly.

Describe concrete discrepancies and tune the source controls responsible for
them. Follow the reference guidance above and report the visible
evidence's limitations alongside the actual compile outcome.

For a requested size at a particular location, read the actual STEP section:

```bash
a3d measure MODEL.step --section-z 0 --section-z 95 --out sections.json
```

Choose heights from the brief; the numbers above are illustrative. The report
contains the plane, final outer width/depth, outer-bounds center, material islands
and inner loops. A finishing operation can change a mouth's final width even when
the source's end profile still has the requested width. Compare centers in a shared
frame for lean or offset. A section hole does not prove an insertion path, and a
finite set of sections does not establish global minimum wall thickness. Python
callers can query `measure_section` or `measure_step` for other oriented planes.
For a critical size affected by lofts or finishing, use `measure_section` in the
source after all edits and assert its measured value against the unchanged brief
target. `examples/surface_shell_build.py` checks the envelope in that same pass
and reports all deviations together. Rechecking only the
edited region can miss changes elsewhere in a smooth surface.

Preserve the previous display GLB before overwriting build outputs, then compare
revisions with `a3d compare BEFORE.glb AFTER.glb --view side`. It uses a shared
orthographic frame without aligning or independently scaling the objects. The
image shows before/after surfaces and projected occupancy change. Red means
visible projection present only before; teal means only after. Occluded changes
may be invisible, and the overlap ratio is not a quality score. Use matching
semantic assembly GLBs; independently placed print layouts are different frames.
