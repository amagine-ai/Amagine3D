# Public assembly authoring example

Adapt `examples/assembly_intent.py` and `examples/assembly_build.py` when learning
the public BRep assembly path. They make a removable pin and blind socket coupon,
with two manufactured colors. This is an API example, not a default architecture
for controls or enclosures. It deliberately has no anti-pullout retention.

After creating the marker, copy both files into the current session workspace. Start
with the user's requirements; choose actual component envelopes, fits, assembly
behavior and printable poses rather than preserving the example's dimensions.

Run in that workspace (the runtime supplies `AMAGINE3D_SKILL_DIR`):

```bash
a3d mark --mark .assembly.generation-start
a3d profile --machine a1-mini --nozzle 0.4 --tool 0 --out assembly_printer-profile.json
cp "$AMAGINE3D_SKILL_DIR/examples/assembly_intent.py" .
cp "$AMAGINE3D_SKILL_DIR/examples/assembly_build.py" .
python3 assembly_intent.py
a3d intent assembly_intent.json
a3d compile assembly_scene.json --marker .assembly.generation-start --intent assembly_intent.json --source assembly_build.py --output-dir .
```

The intent source writes the target once. The compiler runs the build source with
the bound intent, scene and output paths in environment variables. The build
constructs the physical objects, observes the actual interface features, binds
those objects to scene nodes, then exports. No placeholder scene or separate manual
build execution is needed. Inspect the reported five-view PNG with `view_image`.
Feature `direction` follows the intent's outward face-normal convention (the top
socket is `+Z`); insertion `assembly_axis` is independently `-Z`. The signed cutter
placement in source must actually open the socket on that top face.

`checked_cut` captures the actual socket cutter, while `observe` captures the pin.
Their `diameter` measurements describe these endpoints, not the entire holder.
The female diameter is derived from the pin and a per-side gap. Installed hardware
would instead stay out of the `export_assembly` part dictionary, with its seat or
keepout declared and observed on the receiving part.

For a signature with required/optional arguments, query just that function:

```bash
a3d capabilities --symbol write_intent
a3d capabilities --symbol write_scene
a3d capabilities --symbol export_assembly
```
