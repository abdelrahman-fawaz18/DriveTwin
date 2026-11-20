# Contract qualification fixtures

These are test inputs/runners, not a second active receiver or source checkout.

- `legacy_contract_1_0.py` is the frozen original reader, verified byte-for-byte against the earlier data-flow report. Do not reformat or edit it. The active receiver remains `src/evdt/contracts.py` and its profile module.
- `build_cases.py` generates deterministic valid/invalid vectors with explicit expectations. The bootstrap asset/catalog assumptions belong to these qualification fixtures; a future registry change should update the controlled setup while preserving the expected rejection.
- `run_python.py` imports the current receiver, exercises the frozen reader, and validates actual TypeScript adapter output bytes.
- `run_typescript.cjs` loads the shared schema/profile/catalog bundle and executes the compiled reusable TypeScript package.

Use the [explained runner](../../docs/setup/contract-verification.md). Golden corpus/results are saved in immutable evidence directories; generated JavaScript and local dependencies are ignored runtime/build artifacts.
