"""Single source of truth for backend-neutral CAD proof capabilities."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


GEOMETRY_TOLERANCE_MM = 0.05

_CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "id": "coaxial-insertion/v1",
        "connectionKinds": (
            "collar-socket",
            "hinge-pin",
            "peg-socket",
            "pin-socket",
            "press-fit",
            "threaded-insert",
        ),
        "endpointRoles": {"male": ("separate", "solid"), "female": ("cutter",)},
        "geometryChecks": (
            "declared-dimensions",
            "axis-alignment",
            "clearance",
            "engagement",
            "assembly-proximity",
            "support-contact-advisory",
        ),
    },
    {
        "id": "guided-profile/v1",
        "connectionKinds": (
            "dovetail",
            "inset-pocket",
            "retained-slider",
            "tab-slot",
        ),
        "endpointRoles": {"male": ("separate", "solid"), "female": ("cutter",)},
        "geometryChecks": (
            "declared-dimensions",
            "clearance",
            "engagement",
            "assembly-proximity",
            "support-contact-advisory",
        ),
    },
    {
        "id": "surface-contact/v1",
        "connectionKinds": ("glue-face",),
        "endpointRoles": {
            "male": ("separate", "solid"),
            "female": ("separate", "solid"),
        },
        "geometryChecks": (
            "declared-dimensions",
            "assembly-proximity",
        ),
    },
    {
        "id": "snap-retention/v1",
        "connectionKinds": ("snap-fit",),
        "endpointRoles": {
            "male": ("separate", "solid"),
            "female": ("cutter", "solid"),
        },
        "geometryChecks": (
            "declared-dimensions",
            "clearance",
            "engagement",
            "assembly-proximity",
            "support-contact-advisory",
        ),
    },
    {
        "id": "self-tapping-screw/v1",
        "connectionKinds": ("self-tapping-screw",),
        "endpointRoles": {},
        "geometryChecks": ("fastener-witness-volumes", "locator-interfaces"),
    },
)


def proof_capabilities() -> list[dict[str, Any]]:
    return [deepcopy(item) for item in _CAPABILITIES]


def connection_kinds() -> set[str]:
    return {
        kind
        for capability in _CAPABILITIES
        for kind in capability["connectionKinds"]
    }


def capability_for_connection(connection: str) -> dict[str, Any] | None:
    for capability in _CAPABILITIES:
        if connection in capability["connectionKinds"]:
            return deepcopy(capability)
    return None
