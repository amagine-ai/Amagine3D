# Public BRep authoring examples

Choose the example that demonstrates the API structure you need:

- `simple_brep_intent.py` and `simple_brep_build.py`: one rounded body with a
  blind pocket, showing physical feature binding and `export_part()`.
- `assembly_intent.py` and `assembly_build.py`: a removable pin and blind socket
  coupon, showing two manufactured parts, a locating fit and `export_assembly()`.

These are API examples. Develop the actual shape, dimensions, features and part
boundaries from the user's request. For a scene containing a mesh master, use the
public mesh binding path described in `surface-shell.md`; that source finishes at
`write_scene()` and the compiler handles export.

Run in the current session workspace (the runtime supplies `AMAGINE3D_SKILL_DIR`).
Set `example_name=assembly` for the two-part example. The profile below is an
illustrative fallback; use the selected machine/nozzle or point the intent source
at an existing valid profile.

```bash
example_name=simple_brep
a3d mark --mark ".${example_name}.generation-start"
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 --out "${example_name}_printer-profile.json"
cp "$AMAGINE3D_SKILL_DIR/examples/${example_name}_intent.py" .
cp "$AMAGINE3D_SKILL_DIR/examples/${example_name}_build.py" .
python3 "${example_name}_intent.py"
a3d intent "${example_name}_intent.json"
a3d compile "${example_name}_scene.json" --marker ".${example_name}.generation-start" --intent "${example_name}_intent.json" --source "${example_name}_build.py" --output-dir .
```

The intent source writes the target separately. The compiler executes the build
with the bound intent, scene and output paths in environment variables. The build
constructs physical objects, records features, binds those same objects into scene
nodes, then exports. `checked_cut` records the actual cutter used in the body;
`observe` records additive features. Inspect the returned preview with `view_image`.

## Assembly-specific relationships

The pin rests in a blind socket and remains removable; the example provides no
anti-pullout retention. Feature `direction` follows the outward face normal
(the top socket is `+Z`), while insertion `assembly_axis` is independently `-Z`.
The cutter opens the socket on that top face. Endpoint `diameter` measurements
describe the socket and pin; the female diameter is derived from the pin and a
per-side gap. Purchased hardware would remain outside the export part dictionary,
with its required seat or keepout modeled on the receiving part.

For a signature with required/optional arguments, query just that function:

```bash
a3d capabilities --symbol write_intent
a3d capabilities --symbol write_scene
a3d capabilities --symbol export_part
a3d capabilities --symbol export_assembly
```
