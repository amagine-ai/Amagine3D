"""Coordinate ordinary build123d geometry, existing scene bindings and evidence.

Intent remains the user contract. Geometry is rebuilt by the source on every
compile; this class neither replays operations nor edits that contract.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from build123d import Shape

from authoring import AuthoringError, write_scene
import cad_helpers
from geometry_binding import bind_brep_feature
from intent_contract import feature_owner_map, physical_part_names, validate


class BuildSession:
    """Build parts in draft or compile; finish() commits edits to returned copies.

    Before intent, declare part_names and pass part_name to multipart add/cut
    calls. The same source runs through compile after its intent is supplied.
    """

    def __init__(self, source_path: str | Path, *, intent_path: str | Path | None = None,
                 scene_path: str | Path | None = None, out_dir: str | Path | None = None,
                 part_names: Sequence[str] | None = None):
        self.source_path = Path(source_path).expanduser().resolve()
        self.is_draft = os.environ.get("AMAGINE3D_SOURCE_PHASE") == "draft"
        declared = None
        if part_names is not None:
            if (isinstance(part_names, (str, bytes)) or not isinstance(part_names, Sequence)
                    or not part_names or any(not isinstance(name, str) or not name.strip() for name in part_names)
                    or len(set(part_names)) != len(part_names)):
                raise AuthoringError("build parts", ["part_names must contain distinct, nonempty part names"])
            declared = set(part_names)
        managed_intent = os.environ.get("AMAGINE3D_INTENT_PATH") or None
        if self.is_draft and intent_path is not None and (
                managed_intent is None or Path(intent_path).expanduser().resolve() != Path(managed_intent).resolve()):
            raise AuthoringError("draft intent", [
                "explicit intent_path must match a3d draft --intent; select the used contract with --intent"])
        intent = intent_path or managed_intent
        self.intent_path = None
        self._intent = None
        if intent is None and not self.is_draft:
            raise AuthoringError("build session", ["supply intent_path or run through a3d compile"])
        if intent is not None:
            self.intent_path = Path(intent).expanduser().resolve()
            try:
                self._intent = json.loads(self.intent_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise AuthoringError("build session", [f"cannot read {self.intent_path}: {error}"]) from error
            errors = validate(self._intent, self.intent_path.parent)
            if errors:
                raise AuthoringError("build session intent", errors)
            self.name = self._intent["part"]
            self._owners = feature_owner_map(self._intent)
            self._part_names = physical_part_names(self._intent)
            if declared is not None and declared != self._part_names:
                raise AuthoringError("build parts", [f"part_names must match intent parts: {sorted(self._part_names)}"])
        else:
            if declared is None:
                raise AuthoringError("build session", [
                    "draft needs BuildSession(..., part_names=(...)); for an existing intent-bound source use a3d draft SOURCE.py --intent INTENT.json"])
            self.name = self.source_path.stem
            self._owners, self._part_names = {}, declared
        if self.is_draft:
            directory = os.environ.get("AMAGINE3D_DRAFT_DIR")
            if not directory:
                raise AuthoringError("build session", ["draft output requires the managed a3d draft run directory"])
            self.out_dir = Path(directory).resolve()
            self.scene_path = self.out_dir / "draft-scene.json"
        else:
            self.out_dir = Path(out_dir or os.environ.get("AMAGINE3D_OUTPUT_DIR", self.source_path.parent)).resolve()
            self.scene_path = Path(scene_path or os.environ.get(
                "AMAGINE3D_SCENE_PATH", self.out_dir / f"{self.name}_scene.json")).resolve()
        self._parts: dict[str, Shape] = {}
        self._features: dict[str, dict[str, Any]] = {}
        self._evidence = cad_helpers._EvidenceState()
        self._finishing = False

    def _check_mutation(self) -> None:
        if self._finishing:
            raise AuthoringError("build finish", [
                "finish callbacks edit their shape copy; call session mutations before or after finish"])

    def _commit_evidence(self, state) -> None:
        # Preserve identity so enclosing capture scopes see committed changes.
        self._evidence.events[:] = state.events
        self._evidence.features.clear()
        self._evidence.features.update(state.features)
        self._evidence.parameters.clear()
        self._evidence.parameters.update(state.parameters)
        self._evidence.issues[:] = state.issues

    @contextmanager
    def capture(self):
        """Capture helper parameters/evidence; restore this session on failure.

        Native edits still operate on caller-owned geometry. Use finish() to
        commit an edited part, or observe() for a separate physical observation.
        """
        self._check_mutation()
        previous = deepcopy(self._evidence)
        parts, features = self._parts.copy(), self._features.copy()
        try:
            with cad_helpers._evidence_scope(self._evidence):
                yield self
                cad_helpers._raise_deferred_source_issues()
        except BaseException:
            self._parts, self._features = parts, features
            self._commit_evidence(previous)
            raise

    @contextmanager
    def _transaction(self):
        working = deepcopy(self._evidence)
        with cad_helpers._evidence_scope(working):
            yield working
            # Compile mode can return an unchanged body with a deferred error.
            # Emit the existing diagnostic before rolling back, never hide it.
            cad_helpers._raise_deferred_source_issues()
        self._commit_evidence(working)

    def _check_new_feature(self, feature_id: str, part_name: str | None) -> None:
        self._check_mutation()
        if part_name is not None and (not isinstance(part_name, str) or not part_name.strip()):
            raise AuthoringError("build feature", ["part_name must be a nonempty string"])
        if not isinstance(feature_id, str) or not feature_id.strip():
            raise AuthoringError("build feature", ["feature ID must be a nonempty string"])
        if feature_id in self._features or feature_id in self._evidence.features:
            raise AuthoringError("build feature", [f"feature {feature_id!r} is already bound"])

    def _owner_for_new_feature(self, feature_id: str, part_name: str | None = None) -> str:
        self._check_new_feature(feature_id, part_name)
        if self._intent is None:
            owner = part_name or (next(iter(self._part_names)) if len(self._part_names) == 1 else None)
            if owner not in self._part_names:
                raise AuthoringError("build feature", [f"supply part_name from {sorted(self._part_names)} for feature {feature_id!r}"])
            return owner
        if feature_id not in self._owners:
            raise AuthoringError("build feature", [
                f"feature {feature_id!r} is not declared in intent; observe binds an existing requirement. "
                "Construction add/cut operations require an explicit part_name and do not bind requirements."
            ])
        owner = self._owners[feature_id]
        if part_name is not None and part_name != owner:
            raise AuthoringError("build feature", [f"feature {feature_id!r} belongs to {owner!r} in intent, not {part_name!r}"])
        return owner

    def _owner_for_operation(self, feature_id: str, part_name: str | None) -> tuple[str, bool]:
        if self._intent is not None and isinstance(feature_id, str) and feature_id not in self._owners:
            self._check_new_feature(feature_id, part_name)
            if part_name not in self._part_names:
                raise AuthoringError("build operation", [
                    f"construction operation {feature_id!r} requires an explicit part_name from {sorted(self._part_names)}"])
            return part_name, False
        return self._owner_for_new_feature(feature_id, part_name), True

    @staticmethod
    def _solid(shape, *, single: bool = False) -> None:
        if (not isinstance(shape, Shape) or not cad_helpers._valid(shape)
                or not shape.solids() or (single and len(shape.solids()) != 1)):
            raise cad_helpers.BuildInvariantError(
                "operation must return one valid solid" if single else "geometry must contain valid solids")

    def _remember(self, feature_id: str, owner: str, role: str, shape: Shape) -> None:
        self._features[feature_id] = {"owner": owner, "role": role, "shape": deepcopy(shape)}

    def _initial_add(self, feature_id: str, owner: str, shape: Shape, minimum: float) -> None:
        """Record actual initialization of an owner, without claiming a union."""
        if (isinstance(minimum, bool) or not isinstance(minimum, (int, float))
                or not math.isfinite(minimum) or minimum < 0):
            raise cad_helpers.BuildInvariantError("min_added_mm3 must be finite and non-negative")
        self._solid(shape, single=True)
        added = float(shape.volume)
        if not math.isfinite(added) or added <= 0:
            raise cad_helpers.BuildInvariantError("initial add must contain finite positive material volume")
        stats = cad_helpers._stats(shape)
        if added < minimum:
            cad_helpers._defer_source_issue({
                "check": "initial-add", "code": "SOURCE.INITIAL_ADD_BELOW_MINIMUM",
                "featureId": feature_id, "partId": owner,
                "expected": {"minimumAddedMm3": float(minimum)},
                "observed": {"addedMm3": added, "addition": stats},
            }, f"initial add {feature_id!r} contains {added:.6f} mm^3; below min_added_mm3")
            cad_helpers._raise_deferred_source_issues()
        cad_helpers._evidence().events.append({
            "id": feature_id, "kind": "add", "part": owner,
            "added_mm3": added, "tool": stats,
        })

    def add(self, feature_id: str, shape: Shape, *, min_added_mm3: float = 0.001,
            part_name: str | None = None) -> Shape:
        """Add material; undeclared IDs need explicit owner and record only events.

        Returns a copy; submit further edits with finish().
        """
        owner, bind_feature = self._owner_for_operation(feature_id, part_name)
        snapshot = deepcopy(shape)
        self._solid(snapshot)
        with self._transaction():
            if owner in self._parts:
                result = cad_helpers.checked_union(
                    self.part(owner), snapshot, feature_id, min_added_mm3=min_added_mm3, part_name=owner)
            else:
                if not bind_feature:
                    self._initial_add(feature_id, owner, snapshot, min_added_mm3)
                result = deepcopy(snapshot)
            if bind_feature:
                cad_helpers.observe(snapshot, feature_id, role="solid", part_name=owner)
        if bind_feature:
            self._remember(feature_id, owner, "solid", snapshot)
        self._parts[owner] = deepcopy(result)
        return self.part(owner)

    def cut(self, feature_id: str, tool: Shape, *, min_removed_mm3: float = 0.001,
            part_name: str | None = None) -> Shape:
        """Cut material; undeclared IDs need explicit owner and record only events.

        Returns a copy; submit further edits with finish().
        """
        owner, bind_feature = self._owner_for_operation(feature_id, part_name)
        body, snapshot = self.part(owner), deepcopy(tool)
        with self._transaction():
            result = cad_helpers.checked_cut(
                body, snapshot, feature_id, min_removed_mm3=min_removed_mm3, part_name=owner)
        if bind_feature:
            self._remember(feature_id, owner, "cutter", snapshot)
        self._parts[owner] = deepcopy(result)
        return self.part(owner)

    def part(self, part_name: str) -> Shape:
        """Return a copy; modifying it never changes the registered part."""
        if part_name not in self._parts:
            raise AuthoringError("build part", [f"part {part_name!r} has no body; add its solid feature first"])
        return deepcopy(self._parts[part_name])

    def observe(self, feature_id: str, shape: Shape | None = None, *, role: str = "separate",
                part_name: str | None = None) -> None:
        """Bind one solid observation, without adding or removing material.

        Omit shape to observe the owning part at this point in construction.
        A whole-part observation establishes its existence, not a measurement
        of a named floor, wall or opening. Use cut() for opening-tool evidence.
        role="solid" identifies existing material (such as a contained boss)
        for an existing scene contract; it still performs no material union.
        """
        owner = self._owner_for_new_feature(feature_id, part_name)
        if role not in {"separate", "solid"}:
            raise AuthoringError("build observation", ["role must be separate or solid; use cut() for cutters"])
        snapshot = self.part(owner) if shape is None else deepcopy(shape)
        self._solid(snapshot)
        with self._transaction():
            cad_helpers.observe(snapshot, feature_id, role=role, part_name=owner)
        self._remember(feature_id, owner, role, snapshot)

    def finish(self, part_name: str, operation: Callable[[Shape], Shape]) -> Shape:
        """Commit operation(copy) only on success, including its checked evidence.

        Declared fillet/chamfer IDs become observation-only scene bindings;
        implementation-only IDs remain operation evidence. Selectors must refer
        to the callback's copy. A native callback without such events can use
        observe() to name an optional physical observation.
        Call session mutations outside the callback; ordinary cad_helpers run
        inside its isolated evidence scope.
        """
        self._check_mutation()
        source = self.part(part_name)
        bindings = {}
        with self._transaction() as working:
            start = len(working.events)
            self._finishing = True
            try:
                result = operation(source)
            finally:
                self._finishing = False
            cad_helpers._raise_deferred_source_issues()
            self._solid(result, single=True)
            for event in working.events[start:]:
                owner = event.setdefault("part", part_name)
                if owner != part_name:
                    raise AuthoringError("build finish", [f"event {event.get('id')!r} belongs to {owner!r}, not {part_name!r}"])
                if event.get("kind") not in {"fillet", "chamfer"}:
                    continue
                feature_id = event["id"]
                if feature_id not in self._owners:
                    continue
                if self._owner_for_new_feature(feature_id) != part_name:
                    raise AuthoringError("build finish", [f"feature {feature_id!r} belongs to another part"])
                cad_helpers.observe(result, feature_id, role="separate", part_name=part_name)
                bindings[feature_id] = {"owner": part_name, "role": "separate", "shape": deepcopy(result)}
            result = deepcopy(result)
        self._features.update(bindings)
        self._parts[part_name] = result
        return self.part(part_name)

    @staticmethod
    def _interface_recipes(interfaces) -> dict[str, dict]:
        """Derive the existing three screw outputs from their shared axis IDs."""
        recipes = {}
        for interface in interfaces:
            if not isinstance(interface, Mapping) or interface.get("kind") != "self-tapping-screw":
                continue
            fasteners = interface.get("fasteners")
            if not isinstance(fasteners, (list, tuple)):
                continue  # The existing scene validator diagnoses malformed data.
            for fastener in fasteners:
                if not isinstance(fastener, Mapping):
                    continue
                for endpoint, field, output in (("cover", "featureId", "clearance-cutter"),
                                                ("receiver", "featureId", "pilot-cutter"),
                                                ("receiver", "bossFeatureId", "receiver-boss")):
                    record = fastener.get(endpoint)
                    feature_id = record.get(field) if isinstance(record, Mapping) else None
                    if not isinstance(feature_id, str):
                        continue
                    recipe = {"kind": "selfTappingScrewPair", "parameters": {
                        "interfaceId": interface.get("id"), "fastenerId": fastener.get("id"), "output": output}}
                    if feature_id in recipes and recipes[feature_id] != recipe:
                        raise AuthoringError("build interface", [f"feature {feature_id!r} maps to conflicting screw outputs"])
                    recipes[feature_id] = recipe
        return recipes

    def export(self, *, paired_interfaces: Sequence[Mapping[str, Any]] = (),
               interfaces: Sequence[Mapping[str, Any]] = (), materials: Sequence[Mapping[str, Any]] = (),
               installation_checks: Sequence[Mapping[str, Any]] = (),
               part_options: Mapping[str, Mapping[str, Any]] | None = None,
               max_overlap_mm3: float = 0.01, part_colors: dict[str, str] | None = None,
               draft_references: Mapping[str, Shape] | None = None) -> dict[str, Any]:
        """Export the committed parts; submit final shape edits with finish() before this call.

        Raw interfaces, installations and part options retain write_scene's
        contract. Final manufactured-part observations are export-local, so
        repeated export neither duplicates evidence nor rewrites cut evidence.
        In a3d draft, export only the current geometry to its isolated preview.
        draft_references are preview-only component envelopes; final component
        display and installation evidence still use the normal scene contracts.
        """
        self._check_mutation()
        if self.is_draft:
            from cad_draft import export_draft
            construction_features = {feature_id: {"owner": feature["owner"], "role": feature["role"]}
                                     for feature_id, feature in self._features.items()}
            return export_draft(self._parts, references=draft_references,
                                construction_features=construction_features)
        missing = self._part_names - set(self._parts)
        if missing:
            raise AuthoringError("build parts", [f"parts have no geometry: {sorted(missing)}"])
        options = deepcopy(dict(part_options or {}))
        unknown = set(options) - self._part_names
        if unknown:
            raise AuthoringError("build parts", [f"unknown part_options: {sorted(unknown)}"])
        recipes = self._interface_recipes(interfaces)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.scene_path.parent.mkdir(parents=True, exist_ok=True)
        scene_parts = {}
        for owner in sorted(self._parts):
            metadata = options.get(owner, {})
            extra_nodes = list(metadata.pop("nodes", ()))
            nodes = []
            for feature_id, feature in self._features.items():
                if feature["owner"] != owner:
                    continue
                token = sha256(feature_id.encode()).hexdigest()[:24]
                if feature_id in recipes:
                    nodes.append({"id": f"feature-{token}", "featureId": feature_id,
                                  "role": feature["role"], "recipe": recipes[feature_id]})
                    continue
                nodes.append(bind_brep_feature(
                    node_id=f"feature-{token}", feature_id=feature_id, role=feature["role"],
                    shape=feature["shape"], path=self.out_dir / f"{self.name}-feature-{token}.stl"))
            scene_parts[owner] = {"representationMaster": "brep", **metadata, "nodes": [*nodes, *extra_nodes]}
        write_scene(self.scene_path, intent_path=self.intent_path, parts=scene_parts,
                    paired_interfaces=paired_interfaces, interfaces=interfaces,
                    materials=materials, installation_checks=installation_checks)
        paths = {"out_dir": str(self.out_dir), "intent_path": str(self.intent_path),
                 "scene_path": str(self.scene_path), "source_path": str(self.source_path)}
        exported_parts = deepcopy(self._parts)
        with cad_helpers._evidence_scope(deepcopy(self._evidence)):
            for owner, shape in exported_parts.items():
                feature_id = f"manufactured-part/{sha256(owner.encode()).hexdigest()[:24]}"
                while feature_id in self._owners or feature_id in cad_helpers._evidence().features:
                    feature_id += "/final"
                cad_helpers.observe(shape, feature_id, role="separate", part_name=owner)
            if self._intent["manufacturing"]["mode"] == "single-part":
                return cad_helpers.export_part(exported_parts[self.name], self.name, **paths)
            return cad_helpers.export_assembly(exported_parts, self.name, max_overlap_mm3=max_overlap_mm3,
                                               part_colors=part_colors, **paths)
