# Developing product surfaces

Develop the form from the requested character, use and important dimensions.

## Give the volume a character

Translate the brief into relationships you can shape: where the volume is fullest,
how it meets a table or a hand, how its crown and underside develop, and how its
major features sit within the silhouette. Carry proposed proportions into the
existing intent assumptions and expose them as source parameters.

Develop the front and side silhouettes together: a soft front outline can still
read as a bulky extrusion from the side. Place fullness, taper and underside
clearance where they support the intended character; edge rounding alone leaves
the broad planes and volume distribution unchanged. A thin object may rely on its
perimeter, while a grip may need to lean, swell and narrow along the hand.
Give secondary forms a clear scale and connection to the main volume: a deliberate
joint, a recessed overlap or a flowing root. Check the spaces between them as part
of the silhouette; soft individual pieces can still form a stiff stack.

## Choose controls that express the form

Use build123d BRep throughout the manufactured body. For a product enclosure,
start with a small set of key sections and loft between them. Extrusions,
revolutions and sweeps remain useful when they express the intended shape more
directly. `a3d guide strategy` helps choose the construction.

For a lofted surface, let width, depth and section shape develop
independently along its path. Section centers and orientations can follow a lean,
an asymmetric grip or a curved spine. Choose the path direction to suit the
object: its long axis, a handle path or a vertical body profile. Give front and
rear profiles separate controls where they need different behavior, connecting
them through compatible section boundaries. Keep section winding, seam positions
and edge correspondence consistent to avoid twists.

`surface-shell.md` provides a working BRep loft example with a real cavity.
Adapt its key sections, end treatment and opening arrangement to the design.
Model near the local origin and use named transforms for assembly placement.

## Shape the transitions

Use section spacing, profile shape and local blend dimensions to shape the
shoulders and transitions. Smooth lofts, `ruled=True` lofts and several joined
BRep volumes are all useful; a readable silhouette may use visible facets or
coarser transitions. G2 continuity is not a default requirement.

If a loft twists or fails, first simplify profiles, align their correspondence,
adjust spacing or divide the volume into simpler BRep constructions. Add sections
only where they control a specific landmark. Preserve the intended dimensions
and validation checks as the construction changes.

## Integrate the functional details

Place a screen, opening, control or seam in the local frame of its host surface.
Share the outline and placement controls between its visible border, physical
recess or aperture, and the component surface when present. On a curved body,
choose how the detail meets the skin: a tangent plane, a shaped seat or a
conforming surface. `installed-displays.md` covers the physical and display-only
relationships for installed screens.

Where the design includes an interior, develop it from the chosen outer form.
Give the cavity, rim, floor and local supports dimensions tied to that form and
the parts they serve. Subtract a separately controlled inner loft from the outer
solid, or use a BRep offset when it remains valid. An inset in each section does
not establish constant 3D normal thickness: inspect measured wall thickness,
especially at shoulders and end transitions. Extend the cavity cutter through
the service opening and preserve the intended floor and rim.

## Let the preview inform the next edit

Use the current geometry's preview to continue designing. Look at the contour and
the way light moves across the broad surfaces, then tune the source controls that
create those effects. Use available views of that same geometry to understand
the relevant grip, underside or rear detail. If a construction becomes
awkward to adjust, revisit its profiles, section spacing or BRep construction
with the intended form in mind. After a failed operation or change of construction,
use the original landmarks to develop another way to express the requested form
and function.
