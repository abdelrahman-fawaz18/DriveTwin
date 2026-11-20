# Portable geographic launcher scenarios

The source manifest identifies 168 immutable prepared inputs (482,514,000 logical
bytes) for ten geographic trips and four charging variants. The compact source
catalog describes operating assumptions and input identities. The external
scenario bundle supplies OSM corridors, attributed heights, forecast/runtime
networks, passenger-stop plans, and hypothetical charging assignments. It does
not contain bus recordings, credentials, fitted models, or evaluation outcomes.

The bundle is local and ignored. It is not included in the source archive and
has not been published. A fresh checkout needs a separately supplied compatible
bundle to use these ten presets. The smaller Montréal release demonstration has
its own source-only preparation procedure in
[the source guide](../../docs/release/source-package.md).

From Windows PowerShell at the project root, place the separately supplied bundle
under `.private/operator/geographic-v1-bundle`, then run:

```powershell
python scripts/launcher/scenarios.py verify --bundle .private/operator/geographic-v1-bundle
python scripts/launcher/scenarios.py install --bundle .private/operator/geographic-v1-bundle
```

Verification checks every file's size and SHA-256 against the source manifest.
Installation links or copies exact bytes into
`.private/operator/scenarios/geographic-v1`, validates all prepared profiles,
then atomically installs `.private/operator/live-trips.json`. It starts no service
or simulation. Repeating installation verifies the existing inputs; changed or
partial inputs require inspection before replacement. No study is rerun and no
consumed evaluation identity is reset.

New inputs are verified in a uniquely named `geographic-v1.partial-*` directory
before the installed directory becomes available. An interrupted transfer keeps
that partial directory for inspection and leaves the catalog unchanged. Retrying
from the verified bundle starts a fresh transfer without replacing partial data.

An existing compatible installation can prepare a transfer directory with:

```powershell
python scripts/launcher/scenarios.py export --output .private/operator/geographic-v1-bundle
```

Copy that directory as ordinary files when transferring it to another computer.
Local export/install use hard links when supported to avoid duplicate storage.
Treat every linked input as immutable; editing a linked file also changes its
other local names. Deleting one name preserves the remaining names. Preparation
tools write new directories instead of editing these inputs.

Roads derive from OpenStreetMap contributors' Montréal, Ottawa, and Vancouver
regional extracts supplied by BBBike, under ODbL 1.0. Preserve attribution and
applicable derived-database conditions. Heights derive from Natural Resources
Canada high-resolution digital terrain and surface mosaics under the Open
Government Licence – Canada, using CGVD2013. Per-file receipts retain geometry,
height quality, source fingerprints, and declared assumptions. The source
[data inventory](../../docs/data-sources.md) and
[new-map guide](../../docs/setup/geographic-energy.md) describe provenance and
preparation. Installing exact inputs preserves the reviewed input bytes; it does
not extend earlier model accuracy, actuation, or fleet qualification claims.
