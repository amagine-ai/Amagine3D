# Installed-component geometry evidence

Use parameterized envelopes for installed items whose functional relationships
belong in the request. Choose the needed checks by behavior: clearance, insertion,
support, retention and passage. None implies a particular component, enclosure
split, fastening method or bottom cover. A purchased item remains excluded from
manufacturing exports whether or not a preview reference is shown.

## Bind required checks into compile

On the receiving intent feature, declare only the applicable requirements:

```python
{
    "id": "module-mount", "kind": "mount",
    "evidence": "The replaceable module must remain supported in use",
    "acceptance": "Proposed module envelope fits, enters from the service side and rests on its seat",
    "installation_checks": ["clearance", "insertion", "support"],
}
```

The immutable intent records what must be proved; the scene binds current
witness geometry and final part IDs. Use the actual component outline and shared
installation datums. Query signatures together when useful:

```bash
a3d capabilities --symbol bind_installation_check --symbol check_installation
```

```python
from installation_check import bind_installation_check

installation = bind_installation_check(
    feature_id="module-mount", envelope=module_envelope,
    out_dir=OUT / ".installation",
    obstacle_parts=["housing"], support_parts=["housing"],
    insertion_envelope=module_insertion_sweep,
    withdrawal_axis=(1, 0, 0), support_direction=(0, 0, -1),
)
write_scene(..., installation_checks=[installation])
```

No support surrogate is exported: the compiler imports the final semantic STEP
parts and evaluates them against hash-bound witness meshes. Omitting a declared
check, removing its record, using an unknown part, changing a witness after
binding, or failing the geometry blocks successful compilation. The compile
result includes `installationAudit`. With no declared installation requirements,
ordinary CAD work has no additional installation stage.

| Requirement | Supplied evidence | What is measured |
|---|---|---|
| `clearance` | component `envelope`; receiving part in obstacle/support/retainer groups | No material overlap at the installed position |
| `insertion` | `insertion_envelope` and `obstacle_parts` present during insertion | The witness contains the installed envelope and is clear of those obstacles |
| `support` | `support_parts`; optional `support_direction` | A small displacement toward each named support makes contact |
| `retention` | `retainer_parts`, withdrawal axis, free and stop travel | The free interval is unobstructed and the specified stop position contacts each retainer |
| `passage` | connected `passage_envelope` and `passage_parts` | The witness reaches into the component envelope and contains no material from the specified parts |

`support_direction` points toward the support and is independent of insertion
or withdrawal. If omitted, it defaults to the opposite withdrawal direction for
an axial seat. Retainers installed after component insertion should be excluded
from the insertion obstacle group, but included in installed clearance and
retention checks. Select `free_travel_mm` and `stop_travel_mm` from the intended
movement and clearances, not from universal example values.

Author the complete insertion sweep from the actual path. An axis-aligned box
can be exact for a rectangular component translating on that axis; a bounding-box
sweep of a shaped component can be overly conservative. Use the shaped or
segmented sweep when needed rather than simplifying the component to pass.

For a functional aperture, construct the required optical, cable or other
passage volume using its effective cross-section. Extend it from the actual
exterior entry into the component envelope, crossing any intervening walls in full. A
point or centerline does not establish the full needed opening. The checker
proves the supplied connected volume is clear and reaches the component; it
does not infer the correct exterior endpoint or required optical footprint from
a feature name. Record those endpoints and dimensions in feature acceptance
and inspect the exposed structure with preview references hidden.

## Direct construction checks

`check_installation` also accepts in-memory BRep or mesh geometry for feedback
before export. It returns measured overlaps and raises `InstallationCheckError`
on failure. Use this for early checks; bind requirements above when the evidence
must participate in the final compile decision. Early results alone do not prove
that later edits preserved the installation.

Contact checks prove geometric seating and stops, not force, clamping strength,
elasticity, service life or electrical operation. Friction or adhesive retention is
not a geometric withdrawal stop; use the relevant fit and process evidence
instead of inventing a stop solely to satisfy `retention`. Unspecified requirements are
not automatically inferred by the compiler. `installed_module_intent.py` and
`installed_module_build.py` under `examples/` show a complete installation using
proposed dimensions, separately manufactured parts and an optional preview item.
