# Multipart connection selection

Give every printed part a real assembly relationship before geometry. Choose
the connection from how the object will be assembled and serviced; do not make
parts separate merely because a visual seam exists.

## Positive default for serviceable enclosures

When a shell, cover, or base should be removable, ordinary driver access and
purchased hardware are acceptable, and the user has not selected another
fastening method (for example adhesive, snap-fit, dovetail, hinge, or insert
hardware), start with this construction:

1. Add one locating interface, normally `collar_socket()` around the seam or
   two well-spaced `pin_socket()` pairs. The locator controls position and
   resists shear.
2. Add two symmetric M3 plastic thread-forming/self-tapping screw connections
   with `self_tapping_screw_pair()`. The screws clamp the seam; they do not
   replace the locator.
3. Put a clearance hole in the removable part and a blind pilot hole inside a
   printable boss on the receiving part. Put screw access on a reachable face.
4. For a long or flexible perimeter, add fasteners around the seam as needed
   while keeping every axis clear of ports, screen keepouts, thin walls, and
   internal components.

Use a different connection when its assembly behavior is a better match:

| Need | Preferred construction |
|---|---|
| frequent service cycles or higher clamp load | locating interface plus machine screw and threaded insert |
| tool-free opening | snap-fit or dovetail with a deliberate release path |
| rotation | `hinge_pin()` with shared coaxial bore and removable pin |
| sliding control | `retained_slider()` |
| fitted printable panel, lens, or bezel | `inset_pocket()` |
| permanent cosmetic insert | fitted pocket plus explicitly declared adhesive |

For a selected snap-fit or dovetail, still build a real male/female interface
and derive the female dimensions from the male plus the declared clearance. A
snap needs a printable flex arm, lead-in, retention shoulder, and deliberate
release path; a dovetail needs a clear insertion direction and end stop. These
are geometry requirements, even when no specialized helper recipe is used.

`adhesive` and `loose` remain intentional exceptions. They are not substitutes
for a missing connection on an enclosure part.

## M3 printable starting geometry

Use these as configurable starting dimensions for a common 3.0 mm plastic
thread-forming/self-tapping screw with a 0.4 mm FDM process:

| Parameter | Starting value |
|---|---:|
| nominal screw diameter | 3.0 mm |
| removable-part clearance hole | 3.4 mm |
| receiving pilot hole | 2.6 mm |
| receiver boss outside diameter | 7.5 mm |
| thread engagement | 6.0 mm |
| pilot tip clearance | 0.8 mm |
| closed material beyond pilot | 1.2 mm |
| minimum radial boss wall | 1.8 mm |

These values are not universal tolerances. EJOT's thermoplastic guidance puts a
typical pilot near 0.8 times nominal diameter and allows an increase toward
0.88 for stronger or filled materials; TR Fastenings gives 2.4 mm as its ABS
example for a 3.0 mm plastics screw. The 2.6 mm CAD default is therefore a
conservative upper-end FDM starting point, not a substitute for a small hole
coupon when screw family, filament, printer, or process changes. See the
[EJOT DELTA PT design guidance](https://www.ejot.com/medias/sys_master/Industry_Flyer/Industry_Flyer/h11/hc7/9331662782494/EJOT-DELTA-PT-Flyer-08.23-en.pdf)
and [TR Plas-Tech 30 installation guide](https://www.trfastenings.com/Knowledge-Base/Fasteners-for-Plastic/Plas-Tech-30-installation-guide).

Prefer a screw designed for direct fastening into thermoplastic. Record the
actual chosen screw family and adjust the pilot from its supplier guidance.
Choose an available under-head length within the recipe's reported minimum
(cover stack plus engagement) and maximum (cover stack plus blind pilot depth)
instead of letting the screw bottom out.
Purchased screws may appear as `display-only` assembly references, but exclude
them from STEP/STL/3MF printed-part counts and manufacturing booleans.

## One datum per screw

Every screw location owns one stable fastener ID, one origin, and one unit
direction. In the JSON scene this is `fasteners[].id` plus `axis`; `axis_id` is
only the Python helper parameter that receives the same ID. Never enter the
cover-hole center and pilot-hole center separately.

```python
from build123d import Pos, Rot
from interface_recipes import self_tapping_screw_pair

left = self_tapping_screw_pair(
    interface_id="housing-base-service-joint",
    axis_id="side-left",
    cover_thickness_mm=2.4,
)

# Apply the SAME rigid placement to the whole group.
LEFT = Pos(-24, 0, 4) * Rot(X=-90)  # local +Z screw axis becomes object +Y
base = checked_cut(base, LEFT * left.clearance_cutter, "base-clearance-left")
housing = housing + LEFT * left.receiver_boss
housing = checked_cut(housing, LEFT * left.pilot_cutter, "housing-pilot-left")
```

Generate a symmetric partner from a mirrored datum/pattern and apply that
partner transform to its full group. Never mirror only one cutter or hand-copy
signed coordinates.

The pair uses a local `+Z` screw axis with the mating plane at `Z=0`: the cover
is on negative Z and the receiver grows toward positive Z. Rigidly rotate the
full group for side-access or bottom-access screws. The helper returns no screw
solid; its hardware record is explicitly non-manufactured.

In a semantic-scene fastener record, `receiver.minimumRootEmbedMm` is the
minimum positive-Z depth over which the compiled receiver must contain the boss
wall and fuse it into the receiver body. The boss never protrudes across the
mating plane into the negative-Z cover volume. `rootOverlapMm` is not a valid
field.

## Intent and scene records

Declare the assembly target as `connection: "self-tapping-screw"`. Its
`fastening` block ties the locator and every clearance/pilot/boss feature to the
interface:

```json
{
  "id": "housing-base-service-joint",
  "between": ["housing", "base"],
  "connection": "self-tapping-screw",
  "assembly_axis": "+Z",
  "clearance_mm": 0.4,
  "engagement_mm": 6.0,
  "features": [
    "base-collar", "housing-socket",
    "base-clearance-left", "housing-pilot-left", "housing-boss-left",
    "base-clearance-right", "housing-pilot-right", "housing-boss-right"
  ],
  "fastening": {
    "screw_family": "M3 plastic thread-forming/self-tapping",
    "nominal_diameter_mm": 3.0,
    "pilot_diameter_mm": 2.6,
    "clearance_diameter_mm": 3.4,
    "boss_outer_diameter_mm": 7.5,
    "closed_end_mm": 1.2,
    "locator_pairs": [
      {
        "id": "housing-base-locator",
        "male_feature": "base-collar",
        "female_feature": "housing-socket"
      }
    ],
    "fasteners": [
      {
        "id": "side-left",
        "clearance_feature": "base-clearance-left",
        "pilot_feature": "housing-pilot-left",
        "boss_feature": "housing-boss-left"
      },
      {
        "id": "side-right",
        "clearance_feature": "base-clearance-right",
        "pilot_feature": "housing-pilot-right",
        "boss_feature": "housing-boss-right"
      }
    ]
  },
  "acceptance": "the locator positions the seam and two coaxial M3 screw pairs clamp it"
}
```

In the mutable semantic scene, model every locator as its own `collar-socket` or
`pin-socket` interface with the existing `male`/`female` derived-dimension pair.
The self-tapping joint names one or more of those interfaces through
`locatorInterfaceIds`; this supports either one perimeter collar or multiple
well-spaced pin/socket pairs without flattening their pairing. Add one
`fasteners[]` item per screw. That item owns the only `axis.originMm`,
`axis.direction`, screw dimensions, cover thickness, and boss/pilot dimensions.
Its cover cutter, receiver pilot, and receiver boss nodes use
`recipe.kind: "selfTappingScrewPair"` and select the `clearance-cutter`,
`pilot-cutter`, or `receiver-boss` output of that same
`interfaceId`/`fastenerId` instance. Those nodes may not load independent source
meshes or declare their own transforms. `hybrid_compile.py` constructs the
three build123d outputs once and applies one rigid placement to the entire
group, so matching labels cannot conceal misaligned geometry. Scene validation
also cross-checks locator pair IDs and ordered male/female features, fastener
IDs, mapped feature IDs, screw family, and critical diameters against the
immutable intent.

## Acceptance evidence

Before delivery, prove all of the following:

- every printed part participates in a declared interface unless its adhesive
  or loose installation is explicit;
- each screw pair satisfies `pilot < nominal < clearance`;
- the boss is fused to its receiving part, retains the planned radial wall,
  and leaves a blind closed end beyond the pilot;
- the clearance and pilot cuts both remove material and remain reachable by the
  chosen driver direction;
- every clearance/pilot/boss node selects one output of the same procedural
  recipe instance, and the scene axis is a unit vector;
- repeated fasteners come from one symmetric pattern, and no hole or boss
  feature is reused across two axes; and
- the locating interface fits independently of the screws.

For hybrid compilation, require the report's `fastenerGeometryChecks` to pass.
The compiler rejects a disconnected boss and probes the final cover/receiver
meshes with physical witness volumes. A full clearance-cylinder witness and
pilot-cylinder witness must have zero material overlap; annular witnesses prove
the cover land and minimum boss wall; and a solid cylinder proves the blind
end. This catches a blocked hole even when a small center pinhole makes its axis
look open. Every part must remain one fused body. A failed probe is a build
failure, and manufacturing artifacts are written only after the probes pass.
