# Repeatable engineering checks

The shared command runs source checks on Windows through Ubuntu and directly on Linux. The GitHub Actions workflow runs the corresponding source checks.

## Prerequisites and command

Use the [qualified platform](windows-wsl.md), ordinary Ubuntu account, native Engine and ignored settings from `python scripts/configure_local.py`. Install the scanner once with `python scripts/secrets.py install`. Linux uses `python3` and Docker directly, without Windows account settings or private historical reports.

Run in Windows PowerShell from the project folder:

```powershell
python scripts/dev.py check
```

1. Check ordinary Git candidates for private paths/account details and scan a temporary snapshot for secrets.
2. Build `containers/checks.Dockerfile` using locked Python/npm dependencies and immutable Python/uv/Node images. The first run downloads packages; later builds reuse matching layers. Private inputs are excluded.
3. Run offline with a read-only root, a 768 MiB memory limit, two CPUs, and disposable scratch space.
4. Run Ruff style checks, pytest unit tests, strict TypeScript compilation and actual Python/TypeScript validators against common expected cases. Verify original fixtures, adaptation bytes and required-field preservation.
5. Save source/input hashes, image identity, commands and pass/fail under ignored `.runtime/dev-checks/`. Failure returns a nonzero exit status; the console points to diagnostics without printing secret matches.

The source-stability inventory includes the launcher HTML, styles, scripts,
browser qualification scripts, and Windows entry point as well as backend and
dashboard sources. A change during the check invalidates its source-consistency
result. The shared suite still does not replace actual application acceptance.

Expected result: `Checks passed: True` and exit zero. No private report is read. Database recovery, dashboard behavior, workload performance, energy accuracy and command safety retain their separate phase tests.

## Coverage and CI

Ruff covers application Python, unit/foundation/monitoring tests, shared check/configuration/privacy/map/workload tools, P1 qualifiers and active contract corpus/runners. Historical qualification harnesses are not all reformatted; the original reader is immutable. TypeScript uses strict compilation. Mypy checks the selected queue, storage, plant, run-policy, and energy modules with strict settings. The exact module list is recorded in [scripts/checks.py](../../scripts/checks.py). Mypy checks program types before execution; schemas check actual incoming values.

The original P1-A checkpoint passed 43 unit/foundation/queue tests, 339 qualification outcomes and 39 simulation outcomes per language. P1-B1 extends unit coverage to the plant and sampling, simulation cases to 44, and byte-frozen compatibility guards to historical monitoring artifacts. Real broker/database/source recovery is a separate [integration check](operational-uplink.md), not inferred from unit success. The [plant guide](measurement-plant.md) explains the complete-mission numerical checks.

P1-B2's final shared command passed 75 tests, 339 original and 60 simulation outcomes per language, strict TypeScript and four scoped strict Python modules. Actual service qualification separately proves registration/clock/quality behavior, preserved old SQL bytes and role boundaries; see the [registry guide](run-registry-and-quality.md).

The image is reproducible at the source/dependency/platform boundary, not guaranteed byte-identical across all build metadata/hardware. Logs/fingerprints record the actual inputs.

`.github/workflows/checks.yml` installs the same locked scanner and invokes this command on Ubuntu 24.04, with read-only repository permission and no deployment step. The hosted result stays unverified until a remote is authorized and the workflow actually runs.

## Troubleshooting

Read the latest local `commands.log`/`report.json`. Review scanner findings locally without copying secrets into chat/public docs. For missing Docker inspect the selected engine/service; do not change global agent environments. Investigate immutable-fixture failures against the recorded source and inputs. Schema/compiler mismatches are failures against explicit expectations, not grounds for silent validation relaxation.

P0-06 adds workload arithmetic, metric failure/clock-uncertainty cases, map sanitization/path-containment tests, and the license-inventory tool to checked source. CI also defines a separate Montreal fixture job using the committed snapshot, with no live map service. [P0-06 reproduction](foundation-data.md) explains that independent integration check.
