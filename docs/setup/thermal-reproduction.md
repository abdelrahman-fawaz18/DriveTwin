# Thermal study reproduction

Use Windows PowerShell in the canonical DriveTwin folder. The three thermal studies are standalone. The bench study reads the laboratory archive. The fault and intervention studies generate their own simulated observations. This workflow needs Python and Git and runs without starting the DriveTwin services.

## 1. Verify the original results

```powershell
python scripts/thermal/verify_thermal_closeout.py
```

The verifier uses the Python standard library. It compares each frozen release, the files it relied on, and the original results with their recorded **SHA-256 fingerprints**. A fingerprint is a compact record of file contents that changes when the bytes change. The verifier also compares the source bytes with the exact Git revision frozen before each evaluation. It expects the original ignored reports, attempt records, and traces in `evidence/p5/`. Expect this line on success.

```text
Verified 3 Phase 5 freezes and retained evaluations: 13 bench profiles, 48 advisory cases, 72 intervention traces.
```

The verifier only reads files. On a fresh checkout, use the reviewed summaries for the versioned measurement record and restore the original private evidence when full verification is needed. The **reserved** scenarios are the final evaluation cases set aside from development. Their attempt records preserve which release opened them. Keep those records and the original outputs together. [Verification summaries](../verification/thermal-acceptance.md)

## 2. Run the automated checks

Use the scientific runtime recorded in the releases, with Python 3.13.7, NumPy 2.4.3, and SciPy 1.17.1. NumPy works with arrays of readings, and SciPy supplies the routines that fit and advance the thermal model. The [intervention guide](synthetic-thermal-intervention.md) gives the one-time install command for the project's pinned validation dependencies in the ignored analysis folder. If the scientific packages are missing, use the same package installer and folder.

```powershell
python --version
uv --no-cache pip install --python 3.13 --target .runtime/p5-control-win313 numpy==2.4.3 scipy==1.17.1
```

The first line should show `Python 3.13.7`. The second is only needed to install the two packages. Continue with the checks from the same PowerShell window.

```powershell
$env:PYTHONPATH = 'src;scripts/thermal;.runtime/p5-control-win313'
$env:OPENBLAS_NUM_THREADS = '1'
python -m unittest discover -s tests/thermal -v
python -m unittest discover -s tests/charging -p 'test_charging_dispatch.py' -v
```

`PYTHONPATH` gives Python the project and dependency folders. The thread setting keeps numerical work on one computation thread. The first command runs 33 thermal tests and the second runs three checks of the existing charging admission rules. Both finish with `OK`. The advisory guide also shows how to run RTAMT, an independent checker for a rule over time, on the five comparison traces. [Advisory timing guide](synthetic-thermal-fault-qualification.md)

## 3. Rebuild development results when needed

Use fresh output paths for new development runs. Each command writes an ignored result and leaves the frozen releases and reserved outcomes available for inspection.

| Study | Preparation and command | Expected evidence |
|---|---|---|
| Bench winding | Obtain the publisher archive and verify it with the [audit guide](motor-data-audit.md). Run `python scripts/thermal/freeze_motor_profile_split.py` to check the split and `python scripts/thermal/qualify_bench_thermal.py develop --report evidence/p5/reproduced-bench-development.json` to fit and validate. | Matching 43/13/13 profile counts, one selected winding state, and synthetic observer comparisons. |
| Bus advisory | Run `python scripts/thermal/qualify_thermal_faults.py develop --report evidence/p5/reproduced-advisory-development.json`. | A passing candidate using the 20-second persistence rule and a development score for each candidate. |
| Bus intervention | Run `python scripts/thermal/qualify_thermal_intervention.py develop --output evidence/p5/reproduced-intervention-development`. | The forecast gate, matched mission results, and per-run traces. Expect `"passed": true` in the summary. |

The `inspect` actions in the bench and advisory scripts locate the existing reserved outputs. For intervention, use the verifier in step 1 and the [intervention comparison guide](synthetic-thermal-intervention.md). Use the `develop` actions to exercise the workflow without consuming reserved evidence again.
