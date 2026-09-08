# Design reasoning and review

Use this reference when an assembly, moving part, installed component, or failed
QA needs engineering judgment. Scale the depth of review to the task; these are
questions to resolve, not a prescribed sequence or a requirement to add mechanisms.

## Preserve the purpose while improving the construction

Identify what each connection accomplishes: location, load transfer, retention,
motion, disassembly, or service access. An alternative construction is useful when
it retains the required behavior. A locating pin still needs its mating fit even
if reclassified as detail; adhesive is an intentional assembly choice, not an
exception to select because it has fewer checks.

Distinguish source-backed requirements from initial engineering assumptions before
writing intent. Correct mistaken assumptions using new evidence and explain their
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

The standard preview has five views: isometric, front, side, top, and bottom.
Use them for silhouette, count, proportions, spacing, seams, and exposed openings.
Read the current render named in the compile result, not an older matching glob.
Use available views of that same geometry to understand occluded or localized
features. When the current tools support a focused, section or exploded view,
use it as supplemental evidence while retaining the canonical assembly.

Describe concrete discrepancies and tune the source controls responsible for
them. Follow the main skill's image-reading instructions and report the visible
evidence's limitations alongside the actual compile outcome.
