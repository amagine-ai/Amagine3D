# Developing product surfaces

Develop the form from the requested character, use and important dimensions.

## Give the volume a character

Translate the brief into relationships you can shape: where the volume is fullest,
how it meets a table or a hand, how its crown and underside develop, and how its
major features sit within the silhouette. Carry proposed proportions into the
existing intent assumptions and expose them as source parameters.

Develop the broad surfaces so their proportions carry the design. A thin object
may get its character from a broad face and a carefully shaped perimeter; a grip
may need the volume to lean, swell and narrow along the hand. Give secondary forms
their own purpose and scale within that main volume.

## Choose controls that express the form

Choose geometry by the freedom the shape needs. Analytic profiles, revolutions,
sweeps and BRep lofts make dimensioned curves and planar regions easy to edit.
Guided sections, surface patches or a distance field can give a freeform body
more local control. `a3d guide strategy` connects these constructions to the
available BRep and mesh authoring paths.

For a section-driven surface, let width, depth and section shape develop
independently along its path. Section centers and orientations can follow a lean,
an asymmetric grip or a curved spine. Choose the path direction to suit the
object: its long axis, a handle path or a vertical body profile. Give front and
rear profiles separate controls where they need different behavior, connecting
them through shared boundary curves.

When this construction fits, `surface-shell.md` provides a small working example
of varying sections with a real cavity. Adapt the section, end treatment and
opening arrangement to the new design. For a freely blended volume,
`organic_shell.build_organic_shell(...)` accepts an arbitrary signed-distance
field in millimetres, positive inside; use a distance-valued field so its wall
inset retains its physical meaning. Model near the local origin and use named
transforms for assembly placement.

## Shape the transitions

Give adjoining smooth surfaces shared boundaries and compatible tangents. For a
broad flowing transition, shape how curvature changes across the shoulder as well
as where the shoulder starts and ends. Profile handles, guide curves and local
blend dimensions make these decisions editable. Give intentional planes, shallow
facets and crisp boundaries their own clear geometry. Let related edge treatments
share a visual rhythm while allowing different transitions to serve different uses.

For a mesh master, develop the controlling surface and sample it finely where
curvature changes, openings turn or narrow details need support. Keep broad quiet
regions economical. Use consistent vertex correspondence between connected
sections and matched boundaries between patches. Surface shape controls the
silhouette; display normals help its intended smoothness read in the lighting.

## Integrate the functional details

Place a screen, opening, control or seam in the local frame of its host surface.
Share the outline and placement controls between its visible border, physical
recess or aperture, and the component surface when present. On a curved body,
choose how the detail meets the skin: a tangent plane, a shaped seat or a
conforming surface. `installed-displays.md` covers the physical and display-only
relationships for installed screens.

Develop the interior from the chosen outer form. Give the cavity, rim, floor and
local supports dimensions tied to that form and the parts they serve. A surface
offset, a distance-field inset or separately controlled inner profiles offer
different ways to shape it; measure thickness in the direction the construction
actually controls. Bind local BRep additions and cutters to the owning mesh body
to integrate precise interfaces into the material geometry.

## Let the preview inform the next edit

Use the current geometry's preview to continue designing. Look at the contour and
the way light moves across the broad surfaces, then tune the source controls that
create those effects. Choose additional views of that same geometry when they
help you understand a grip, underside or rear detail. If a construction becomes
awkward to adjust, revisit its profiles, surface boundaries or representation
with the intended form in mind.
