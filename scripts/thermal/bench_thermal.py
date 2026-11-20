"""Read approved bench signals and calibrate temperature prediction by whole profile."""

import csv
import io
import json
import math
import platform
import zipfile
from array import array
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import scipy
from audit_motor_data import sha
from freeze_motor_profile_split import ARCHIVE, MANIFEST, ROOT
from scipy.optimize import least_squares
from thermal_network import HeatNetwork

POLICY = ROOT / "qualification/thermal/bench-development-1.0.0.json"
INPUT_CONTRACT = ROOT / "qualification/thermal/motor-input-contract-1.0.0.json"
A2_RECORD = ROOT / "docs/verification/motor-profile-split.json"
VERSION = "bench-thermal-winding/1.0.0"
FEATURES = ("ambient_k", "coolant_k", "speed_rad_s", "current_squared_a2")
# Rates are conductance / capacity in inverse seconds. Heating gains are K/s per scaled input.
CANDIDATES = {
    "single": {
        "names": ("ambient_rate", "coolant_rate", "current_gain", "speed_gain"),
        "lower": (1e-5, 1e-5, 1e-5, 1e-5),
        "upper": (0.03, 0.1, 1.0, 0.5),
        "start": (0.001, 0.004, 0.09, 0.012),
    },
    "coupled": {
        "names": (
            "coupling_rate",
            "body_ambient_rate",
            "body_coolant_rate",
            "winding_coolant_rate",
            "body_capacity_ratio",
            "current_gain",
            "speed_gain",
        ),
        "lower": (1e-5, 1e-5, 1e-5, 1e-5, 1.0, 1e-5, 1e-5),
        "upper": (0.12, 0.03, 0.1, 0.1, 25.0, 1.0, 0.5),
        "start": (0.008, 0.0008, 0.0015, 0.001, 4.0, 0.09, 0.012),
    },
}


def read(path):
    return json.loads(path.read_bytes())


def write(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(data, indent=2, allow_nan=False) + "\n")


def digest(path):
    with path.open("rb") as stream:
        return sha(stream)


def runtime():
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
    }


def inputs():
    policy, split, contract = map(read, (POLICY, MANIFEST, INPUT_CONTRACT))
    a2 = read(A2_RECORD)
    frozen = {row["path"]: row["sha256"] for row in a2["artifacts"]}
    if not a2["passed"] or frozen != {
        path.relative_to(ROOT).as_posix(): digest(path) for path in (MANIFEST, INPUT_CONTRACT)
    }:
        raise ValueError("Frozen A2 partition or input contract changed")
    if runtime() != policy["runtime"]:
        raise ValueError("Use the recorded scientific Python environment")
    if split["source"]["archive_sha256"] != digest(ARCHIVE):
        raise ValueError("Source archive fingerprint differs from the frozen A2 split")
    if (
        contract["bench_winding_prediction"]["sample_period_s"]
        != split["source"]["sample_period_s"]
    ):
        raise ValueError("A2 sampling contract changed")
    return policy, split


@dataclass
class Profile:
    identity: int
    features: np.ndarray
    winding: np.ndarray
    torque: np.ndarray


def load_profiles(split, names):
    partitions = split["partitions"]
    wanted = {i for name in names for i in partitions[name]["profile_ids"]}
    chunks = {}
    with (
        zipfile.ZipFile(ARCHIVE) as archive,
        archive.open(split["source"]["csv_member"]) as raw,
        io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream,
    ):
        reader = csv.reader(stream, strict=True)
        header = next(reader)
        pid, ambient, coolant, speed, i_d, i_q, winding, torque = map(
            header.index,
            (
                "profile_id",
                "ambient",
                "coolant",
                "motor_speed",
                "i_d",
                "i_q",
                "stator_winding",
                "torque",
            ),
        )
        for row in reader:
            identity = int(row[pid])
            if identity not in wanted:
                continue  # Only the profile ID is inspected for an excluded row.
            buffer = chunks.get(identity)
            if buffer is None:
                buffer = chunks[identity] = array("d")
            buffer.extend(
                (
                    float(row[ambient]) + 273.15,
                    float(row[coolant]) + 273.15,
                    float(row[speed]) * math.tau / 60,
                    float(row[i_d]) ** 2 + float(row[i_q]) ** 2,
                    float(row[winding]) + 273.15,
                    float(row[torque]),
                )
            )
    output = {}
    for name in names:
        profiles = []
        for identity in partitions[name]["profile_ids"]:
            values = np.frombuffer(chunks[identity], dtype=np.float64).reshape(-1, 6)
            if (
                len(values) < 2
                or not np.all(np.isfinite(values))
                or np.any(values[:, [0, 1, 4]] <= 0)
            ):
                raise ValueError(f"Invalid inputs in profile {identity}")
            profiles.append(Profile(identity, values[:, :4], values[:, 4], values[:, 5]))
        if sum(len(profile.winding) for profile in profiles) != partitions[name]["samples"]:
            raise ValueError(f"Unexpected sample inventory in {name}")
        output[name] = profiles
    return output


def force(features, policy):
    return np.column_stack(
        (
            features[:, :2],
            features[:, 3] / policy["current_squared_reference_a2"],
            (features[:, 2] / policy["speed_reference_rad_s"]) ** 2,
        )
    )


def assemble(name, p, policy, dt):
    capacity = policy["capacity_reference_j_per_k"]
    if name == "single":
        ambient, coolant, current, speed = p
        network = HeatNetwork((capacity,), (ambient * capacity,), (coolant * capacity,))
        gains = np.array([[current], [speed]]) * capacity
    elif name == "coupled":
        coupling, ambient, coolant, winding_coolant, ratio, current, speed = p
        body = capacity * ratio
        network = HeatNetwork(
            (capacity, body),
            (0.0, ambient * body),
            (winding_coolant * capacity, coolant * body),
            coupling * capacity,
        )
        gains = np.array([[current * capacity, 0.0], [0.0, speed * body]])
    else:
        raise ValueError("Unknown thermal model")
    return network.discrete(dt), gains


def predict(model, gains, forcing, first):
    initial = [first]
    if len(model.decay) == 2:
        # A neutral initial estimate for the unobserved effective motor body.
        initial.append(0.5 * (first + forcing[0, 1]))
    return model.rollout(initial, forcing[:, 0], forcing[:, 1], forcing[:, 2:] @ gains)[:, 0]


def fit(name, profiles, policy, dt):
    block = policy["fit_block_samples"]
    training = []
    for profile in profiles:
        length = (len(profile.winding) - 1) // block * block
        forcing = force(profile.features[:length], policy).reshape(-1, block, 4).mean(axis=1)
        target = profile.winding[block : length + 1 : block]
        training.append((forcing, float(profile.winding[0]), target, math.sqrt(len(target))))

    def residual(log_p):
        model, gains = assemble(name, np.exp(log_p), policy, dt * block)
        return np.concatenate(
            [
                (predict(model, gains, forcing, first) - target) / weight
                for forcing, first, target, weight in training
            ]
        )

    spec = CANDIDATES[name]
    result = least_squares(
        residual,
        np.log(spec["start"]),
        bounds=(np.log(spec["lower"]), np.log(spec["upper"])),
        max_nfev=policy["max_optimizer_evaluations"],
        ftol=1e-5,
        xtol=1e-5,
        gtol=1e-5,
    )
    if not result.success:
        raise ValueError(f"{name} calibration did not converge: {result.message}")
    return {
        "name": name,
        "parameters": dict(zip(spec["names"], np.exp(result.x).tolist(), strict=True)),
        "evaluations": result.nfev,
        "training_profiles": len(profiles),
        "training_profile_balanced_rmse_k": float(
            np.linalg.norm(result.fun) / math.sqrt(len(profiles))
        ),
    }


def predict_profile(candidate, profile, policy, dt, ambient_start=False):
    name = candidate["name"]
    params = [candidate["parameters"][key] for key in CANDIDATES[name]["names"]]
    model, gains = assemble(name, params, policy, dt)
    first = profile.features[0, 0] if ambient_start else profile.winding[0]
    return predict(model, gains, force(profile.features[:-1], policy), float(first))


def assess(candidate, profiles, policy, dt, radius=None, ambient_start=False):
    rows = []
    regime_thresholds = policy["operating_regime_screen"]
    for profile in profiles:
        predicted = predict_profile(candidate, profile, policy, dt, ambient_start)
        error = predicted - profile.winding[1:]
        ae = np.abs(error)
        speed, current = profile.features[:-1, 2:].T
        regimes = {
            "high_current": current >= regime_thresholds["high_current_squared_a2"],
            "other_current": current < regime_thresholds["high_current_squared_a2"],
            "near_zero_speed": np.abs(speed) < regime_thresholds["near_zero_speed_rad_s"],
            "negative_shaft_power": speed * profile.torque[:-1] < 0,
        }
        rows.append(
            {
                "profile_id": profile.identity,
                "samples": len(error),
                "mae_k": float(ae.mean()),
                "bias_k": float(error.mean()),
                "rmse_k": float(np.sqrt(np.mean(error**2))),
                "peak_absolute_error_k": float(ae.max()),
                "error_quantile_k": float(
                    np.quantile(ae, policy["interval"]["within_profile_error_quantile"])
                ),
                "coverage": None if radius is None else float(np.mean(ae <= radius)),
                "persistence_mae_k": float(
                    np.mean(
                        np.abs(
                            (profile.features[0, 0] if ambient_start else profile.winding[0])
                            - profile.winding[1:]
                        )
                    )
                ),
                "regimes": {
                    key: {
                        "samples": int(mask.sum()),
                        "mae_k": float(ae[mask].mean()),
                        "bias_k": float(error[mask].mean()),
                    }
                    for key, mask in regimes.items()
                    if mask.any()
                },
            }
        )
    return {
        "profiles": len(rows),
        "samples": sum(row["samples"] for row in rows),
        "mean_profile_mae_k": float(np.mean([row["mae_k"] for row in rows])),
        "worst_profile_mae_k": max(row["mae_k"] for row in rows),
        "maximum_absolute_error_k": max(row["peak_absolute_error_k"] for row in rows),
        "mean_profile_interval_coverage": None
        if radius is None
        else float(np.mean([row["coverage"] for row in rows])),
        "mean_persistence_mae_k": float(np.mean([row["persistence_mae_k"] for row in rows])),
        "profiles_detail": rows,
    }


def verification_files():
    return (
        POLICY,
        MANIFEST,
        INPUT_CONTRACT,
        A2_RECORD,
        Path(__file__),
        ROOT / "scripts/thermal/thermal_network.py",
        ROOT / "scripts/thermal/qualify_bench_thermal.py",
        ROOT / "scripts/thermal/bench_observability.py",
        ROOT / "scripts/thermal/freeze_motor_profile_split.py",
        ROOT / "scripts/thermal/audit_motor_data.py",
        ROOT / "tests/thermal/test_thermal_network.py",
    )


def source_hashes():
    return {path.relative_to(ROOT).as_posix(): digest(path) for path in verification_files()}
