"""Read back final semantic STEP parts and evaluate declared installation witnesses."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from installation_contract import validate_installations
from installation_check import check_installation, InstallationCheckError


def audit(report_path):
    from build123d import import_step
    import trimesh

    path = Path(report_path).resolve()
    result = {"schema": "evidence-installation-audit/v1", "pass": False,
              "errors": [], "checks": [], "inputs": {}, "witnesses": []}

    def load_bound(binding, root):
        if not isinstance(binding, dict) or not isinstance(binding.get("path"), str):
            raise ValueError("missing artifact binding")
        source = (root / binding["path"]).resolve()
        if sha256(source.read_bytes()).hexdigest() != binding.get("sha256"):
            raise ValueError(f"hash mismatch: {source.name}")
        return source

    try:
        report = json.loads(path.read_text())
        scene_path = load_bound(report["inputs"]["scene"], path.parent)
        intent_path = load_bound(report["inputs"]["intent"], path.parent)
        scene = json.loads(scene_path.read_text())
        intent = json.loads(intent_path.read_text())
        records = scene.get("installationChecks", [])
        errors = validate_installations(records, intent, set(report["parts"]), scene_path.parent)
        if errors:
            raise ValueError("; ".join(errors))
        result["inputs"] = {"scene": report["inputs"]["scene"], "intent": report["inputs"]["intent"],
                            "buildReport": {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}}
        parts = {}

        def part(name):
            if name not in parts:
                binding = report["artifacts"][f"step:{name}"]
                source = load_bound(binding, path.parent)
                # STEP preserves assembly coordinates; plate meshes do not.
                parts[name] = import_step(source)
                result["inputs"][f"step:{name}"] = binding
            return parts[name]

        def witness(record, field):
            binding = record[field]
            source = load_bound(binding, scene_path.parent)
            result["witnesses"].append({"featureId": record["featureId"], "kind": field, **binding})
            return trimesh.load_mesh(source, process=True)

        for record in records:
            feature = record["featureId"]
            envelope = witness(record, "envelope")
            groups = {key: {name: part(name) for name in record.get(field, [])}
                      for key, field in (("obstacles", "obstacleParts"), ("supports", "supportParts"),
                                         ("retainers", "retainerParts"))}

            def collect(call, prefix=""):
                try:
                    evidence = call()
                except InstallationCheckError as error:
                    evidence = error.report
                for check in evidence["checks"]:
                    item = {**check, "featureId": feature, "id": f"{feature}/{prefix}{check['id']}"}
                    result["checks"].append(item)
                    if not item["pass"]:
                        result["errors"].append({"code": "QA.INSTALLATION_FAILED", "check": item["id"],
                                                 "message": f"{item['id']}: {item['observed']}",
                                                 "details": item})

            if any(groups.values()):
                collect(lambda: check_installation(
                    envelope, **groups,
                    insertion_envelope=witness(record, "insertionEnvelope") if "insertionEnvelope" in record else None,
                    withdrawal_axis=tuple(record["withdrawalAxis"]), contact_probe_mm=record["contactProbeMm"],
                    support_direction=record.get("supportDirection"),
                    free_travel_mm=record.get("freeTravelMm"), stop_travel_mm=record.get("stopTravelMm"),
                    max_overlap_mm3=record["maxOverlapMm3"],
                ))
            if "passageEnvelope" in record:
                passage = witness(record, "passageEnvelope")
                if not passage.is_volume or len(passage.split()) != 1:
                    raise ValueError(f"{feature}: passage envelope must be one connected volume")
                connection = trimesh.boolean.intersection([passage, envelope], engine="manifold")
                if connection.is_empty or connection.volume <= record["maxOverlapMm3"]:
                    raise ValueError(f"{feature}: passage envelope must extend into its component envelope")
                collect(lambda: check_installation(passage,
                    {name: part(name) for name in record["passageParts"]},
                    max_overlap_mm3=record["maxOverlapMm3"]), "passage/")
        result["limitations"] = "Authored component and path envelopes; passage checks establish a connected clear witness volume reaching the component, not an inferred exterior endpoint or optical footprint. No force, deformation or electrical-function proof. Undeclared installation requirements are not inferred."
    except Exception as error:
        result["errors"].append(str(error))
    result["pass"] = not result["errors"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    result = audit(args.report)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
