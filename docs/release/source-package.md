# Prepare the local simulation source

Use PowerShell from the project folder. The launcher runs the preparation inside Ubuntu. It checks for the pinned database and message-broker images and the simulator image. If those images are missing, it pulls or builds them from the recorded inputs.

```powershell
python scripts/configure_local.py
python scripts/linux.py --timeout 1000 -- python3 scripts/release/prepare_simulation.py
```

On a fresh checkout, this also generates the charging network from the reviewed OpenStreetMap snapshot. The road converter adds a timestamp and paths to its first XML comment. The preparation script restores the recorded build header, then compares the complete file with the frozen SHA-256 fingerprint. A mismatched network stops preparation. An existing valid network stays in place. Use `--reconstruct-network` to reproduce the comparison with an existing copy.

The script generates only the map input. The previously qualified charging, cooling, and model evaluation reports remain separate. [Map attribution](../../data/public/montreal-core-v1/README.md) · [Charging and cooling verification](../setup/charging-and-cooling-checks.md)

## Fresh-checkout prerequisites by capability

| Capability | Source supplies | Separate prerequisite and verification |
|---|---|---|
| Base Montréal release demo | Reviewed road snapshot, map recipe, scenario definitions, dependency locks and image pins | Install the documented Windows/Ubuntu/Docker tools, generate ignored local settings, then run the preparation above. Use `--dashboard` to build browser assets. No retained evaluation run is needed for map preparation. |
| Ten geographic launcher presets | Portable catalog and exact 168-file input manifest | A separately supplied compatible [scenario bundle](../../simulation/geographic/README.md), 482,514,000 logical bytes. The source archive excludes it. Verify and install with the commands below; this starts no bus or service. |
| A new geographic map | Route/elevation preparation code, contracts and locked preprocessing dependencies | Attributable OSM extract, suitable terrain/surface input or supported provider coverage, vehicle profile and declared assumptions. Follow the [new-map guide](../setup/geographic-energy.md). Route-specific retraining is unnecessary; a reference drive or evaluation is a separate authorized workload. |
| Local recorded browser story | Semantic export/player code and verification/package tools | An accepted private static candidate for ordinary serving. Rebuilding needs the original matched protocol, charge/continue recordings, backups and export audits, excluded from source archives. [Browser guide](../setup/browser-demo.md) |
| Historical recording restore | Restore code and documented inventory checks | Retained private backup, integrity manifest and matching application image. [Restore procedure](#restore-a-recording) |
| Frozen model/study inspection | Original contracts, recipes, compact scoped results and consumed-evaluation identities | The private artifact set named by its inspector. Inspect retained results; do not regenerate consumed outcomes. [Legacy energy](../setup/held-out-energy-evaluation.md), [thermal inspection](../setup/thermal-reproduction.md) |

In Windows PowerShell from the project root, after placing a compatible external bundle under the documented ignored path:

```powershell
python scripts/launcher/scenarios.py verify --bundle .private/operator/geographic-v1-bundle
python scripts/launcher/scenarios.py install --bundle .private/operator/geographic-v1-bundle
```

The installer checks sizes and SHA-256 fingerprints, installs immutable inputs under `.private/operator/scenarios/geographic-v1`, validates the profiles and atomically selects the local catalog. It preserves OSM geography, elevation provenance and the common forecast model. The installed catalog needs no abandoned evaluation directory or personal absolute path. The external bundle remains a distribution prerequisite, not an implicitly public dataset.

## Current source checks and historical inspectors

The source archive contains every tracked file, including contracts, dependency/data notices, model recipes and sanitized source-bound reports. Documentation curation is reflected in the committed tree; packaging does not silently exclude an unlinked document. The [capability matrix](capabilities.md) identifies each current path's evidence.

Ordinary offline source/contract checks and builds run from a prepared fresh checkout without private recordings. Historical tools may additionally need private data or development Git history. For example, `scripts/thermal/verify_thermal_closeout.py` checks frozen development commits using `git show`; the public repository's single initial commit cannot supply that history. Run source-bound historical inspectors with the retained development revision and inputs.

Source-fingerprint inspection can also refuse a changed current checkout. Use the recorded source and compatible inputs for historical inspection; do not update an old claim to match newer code. A source build, retained-recording check and new live qualification are distinct results.

The source release groups files by function. Historical reports retain configuration identifiers and source fingerprints from their original runs. Use the corresponding retained development revision for checks that bind to those original paths or hashes.

## Build a private source snapshot

From a clean, committed working tree, use a new label.

```powershell
python scripts/release/package_source.py --name local-review
```

The command writes `EV-DT-source.zip` and `source-manifest.json` under the ignored `.private/releases/local-review/` folder. It compares every archive member with the Git commit and checks the archived contents for private paths and known credential patterns. The archive contains source and documented inputs. Installed programs, local configurations, recordings, and credentials remain outside it.

The versioned manifest identifies the commit, archive fingerprint, dependency lock fingerprints, image pin fingerprints, map source record, and applicable evidence index. It also records the acceptance status at that commit. Use a Git checkout at the named commit for tests that verify Git source fingerprints. [Release evidence](verification.md) · [Privacy checks](../privacy.md) · [Rights inventory](../rights/README.md)

The release handoff is the reviewed source archive. It contains tracked source files and excludes Git repository metadata. The public repository is initialized from the approved archive with one commit on `main`. [Publication procedure](publication.md) · [Source reproduction and privacy results](../verification/source-reproduction.json)

## Restore a recording

Keep the private backup, its integrity manifest, and the matching original application image together. The source package supplies the restore code and documentation. The operator supplies the private recording and matching image. The current-reader check starts a new isolated database, verifies the restored inventory, and then runs the current reader against it. Its recorded host precheck must pass before startup. [Current reader procedure](../setup/history-query-and-restore.md#current-reader-compatibility) · [Normal-queue restoration review](../verification/restoration-review.json)

## Source package and publication rights

| Material | Package treatment |
|---|---|
| Original DriveTwin code and synthetic scenarios | Included under the [MIT license](../../LICENSE). The root license covers original code, documentation, and synthetic scenarios. |
| Reviewed Montréal road snapshot | Included with OpenStreetMap contributor credit, the source record, and Open Database License 1.0 terms. The charging network is reconstructed locally from that snapshot. [Repository attribution](../../data/public/montreal-core-v1/README.md) · [OpenStreetMap copyright](https://www.openstreetmap.org/copyright) · [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/) |
| Installed libraries, browsers, and images | The archive contains the dependency locks and build recipes. Installed files are built or fetched separately. Distribution of a built binary or container requires review of that artifact's notices and contents. [Software and notices inventory](../rights/README.md) |
| Recordings, backups, and lab source data | Kept in local ignored storage. The source archive includes reviewed recipes and aggregate evidence summaries. [Data provenance](../data-sources.md) |
