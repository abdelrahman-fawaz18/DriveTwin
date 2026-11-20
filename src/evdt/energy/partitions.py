"""Whole-mission partition rules; used by the harness, excluded from the worker."""

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal, TypeVar

from evdt.energy.jobs import exact_object, record
from evdt.energy.observations import fingerprint, integer, number, text

Partition = Literal["train", "validation", "held_out"]
PARTITIONS = ("train", "validation", "held_out")
K = TypeVar("K")


def _claim(owners: dict[K, str], key: K, partition: str) -> None:
    if key in owners and owners[key] != partition:
        raise ValueError("Causal family, recipe or realization seed crosses partitions")
    owners[key] = partition


def digest(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Expected SHA-256 digest")
    return value


@dataclass(frozen=True)
class MissionRecipe:
    route_id: str
    realization_seed: int
    initial_soc: float
    loaded_mass_kg: float
    auxiliary_power_w: float
    ambient_temperature_k: float
    actual_capacity_kwh: float
    declared_capacity_kwh: float
    traffic_regime: str
    scheduled_stop_s: float

    def __post_init__(self) -> None:
        text(self.route_id, "route_id")
        text(self.traffic_regime, "traffic_regime")
        integer(self.realization_seed, "realization_seed")
        if not 0 <= number(self.initial_soc, "initial_soc") <= 1:
            raise ValueError("Invalid initial charge")
        for name in (
            "loaded_mass_kg",
            "ambient_temperature_k",
            "actual_capacity_kwh",
            "declared_capacity_kwh",
        ):
            if number(getattr(self, name), name) <= 0:
                raise ValueError("Positive physical recipe quantity required")
        for name in ("auxiliary_power_w", "scheduled_stop_s"):
            if number(getattr(self, name), name) < 0:
                raise ValueError("Nonnegative recipe quantity required")


@dataclass(frozen=True)
class MissionAssignment:
    mission_id: str
    family_id: str
    regime: str
    partition: Partition
    recipe: MissionRecipe

    @property
    def recipe_sha256(self) -> str:
        return fingerprint(asdict(self.recipe))


@dataclass(frozen=True)
class PartitionDesign:
    design_id: str
    missions: tuple[MissionAssignment, ...]

    @property
    def design_sha256(self) -> str:
        return fingerprint(asdict(self))


def parse_design(data: Any) -> PartitionDesign:
    exact_object(data, {"version", "design_id", "status", "regimes", "missions"})
    if (
        data["version"] != "energy-mission-design/1.0.0"
        or data["status"] != "planned_not_collected"
    ):
        raise ValueError("Unknown design or collection status")
    text(data["design_id"], "design_id")
    regimes = data["regimes"]
    if (
        not isinstance(regimes, list)
        or not regimes
        or not all(isinstance(r, str) and r for r in regimes)
    ):
        raise ValueError("Explicit regimes required")
    if len(set(regimes)) != len(regimes):
        raise ValueError("Duplicate regime")
    rows = data["missions"]
    if not isinstance(rows, list) or not 0 < len(rows) <= 10_000:
        raise ValueError("Mission count budget exceeded")
    assignments = []
    identifiers: set[str] = set()
    family_owner: dict[str, str] = {}
    recipe_owner: dict[str, str] = {}
    seed_owner: dict[int, str] = {}
    coverage: set[tuple[str, str]] = set()
    for row in rows:
        exact_object(row, {"mission_id", "family_id", "regime", "partition", "recipe"})
        for key in ("mission_id", "family_id", "regime"):
            text(row[key], key)
        if row["partition"] not in PARTITIONS or row["regime"] not in regimes:
            raise ValueError("Unknown partition or regime")
        if row["mission_id"] in identifiers:
            raise ValueError("Duplicate mission")
        identifiers.add(row["mission_id"])
        recipe = record(row["recipe"], MissionRecipe)
        assignment = MissionAssignment(
            row["mission_id"], row["family_id"], row["regime"], row["partition"], recipe
        )
        _claim(family_owner, assignment.family_id, assignment.partition)
        _claim(recipe_owner, assignment.recipe_sha256, assignment.partition)
        _claim(seed_owner, recipe.realization_seed, assignment.partition)
        coverage.add((assignment.regime, assignment.partition))
        assignments.append(assignment)
    if coverage != {(r, p) for r in regimes for p in PARTITIONS}:
        raise ValueError("Every regime needs a whole mission in every partition")
    return PartitionDesign(data["design_id"], tuple(assignments))


@dataclass(frozen=True)
class MissionArtifact:
    artifact_id: str
    mission_id: str
    family_id: str
    partition: Partition
    recipe_sha256: str
    causal_trace_sha256: str
    observation_content_sha256: str


def validate_inventory(
    design: PartitionDesign, artifacts: tuple[MissionArtifact, ...], *, require_complete: bool
) -> None:
    """Bind collected artifacts to the frozen design, including derived/replayed copies.

    Content digests must be computed by the trusted collector without run/asset IDs,
    timestamps of collection, or file paths. Declared hashes alone cannot prove that
    collection was honest. This function is a harness guard, not a file permission.
    """
    if not isinstance(artifacts, tuple) or len(artifacts) > 100_000:
        raise ValueError("Artifact count budget exceeded")
    missions = {m.mission_id: m for m in design.missions}
    ids: set[str] = set()
    present: set[str] = set()
    content_owners: dict[str, str] = {}
    for artifact in artifacts:
        text(artifact.artifact_id, "artifact_id")
        if artifact.artifact_id in ids:
            raise ValueError("Duplicate artifact")
        ids.add(artifact.artifact_id)
        mission = missions.get(artifact.mission_id)
        if mission is None or (artifact.family_id, artifact.partition, artifact.recipe_sha256) != (
            mission.family_id,
            mission.partition,
            mission.recipe_sha256,
        ):
            raise ValueError("Artifact does not match frozen mission assignment")
        for value in (
            artifact.recipe_sha256,
            artifact.causal_trace_sha256,
            artifact.observation_content_sha256,
        ):
            digest(value)
        # Use one namespace: a copied trace cannot be laundered as another artifact kind.
        for value in (artifact.causal_trace_sha256, artifact.observation_content_sha256):
            if value in content_owners and content_owners[value] != artifact.partition:
                raise ValueError("Copied mission content crosses partitions")
            content_owners[value] = artifact.partition
        present.add(mission.mission_id)
    if require_complete and present != set(missions):
        raise ValueError("Dataset is incomplete")


def authorize_partition(
    partition: Partition,
    purpose: str,
    *,
    calibration_sha256: str | None = None,
    targets_sha256: str | None = None,
    protocol_sha256: str | None = None,
) -> None:
    """A harness gate; held-out evaluation needs frozen model, targets and protocol."""
    allowed = {"fit": "train", "tune": "validation", "evaluate": "held_out"}
    if allowed.get(purpose) != partition:
        raise ValueError("Partition is not allowed for this purpose")
    if partition == "held_out":
        for value in (calibration_sha256, targets_sha256, protocol_sha256):
            if value is None:
                raise ValueError(
                    "Freeze calibration, targets and protocol before held-out evaluation"
                )
            digest(value)
