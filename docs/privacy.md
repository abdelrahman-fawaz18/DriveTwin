# Repository privacy and publication

The source repository contains independently authored code, reviewed public inputs, synthetic scenarios, dependency locks, and sanitized verification summaries. Credentials and operational data stay in local ignored storage.

## Public candidates and private material

| Included in source review | Local and ignored |
|---|---|
| Source, tests, schemas, catalogs, container recipes, and lockfiles | Credentials, private keys, account settings, local environment files, and generated Compose overrides |
| Engineering documentation and reviewed aggregate results | Conversations, personal screenshots, raw diagnostics, browser profiles, and traces |
| Attributed public map subset and source metadata | Unreviewed downloads, original laboratory archive, raw simulated recordings, and fitted private artifacts |
| Synthetic recipes and fingerprints of evaluated inputs | Database contents, queue files, backups, per-run identities, and complete model job inputs and outputs |

`python scripts/configure_local.py` creates ignored `environments/dev.toml` from the reviewed settings template. `.runtime/` holds generated credentials, packages, caches, and temporary state. `evidence/` holds raw results and diagnostics. `.private/` holds private review packages and local records. Git tracks the reviewed summaries that reference those results. [Settings template](../environments/dev.example.toml) · [Source package](release/source-package.md)

API credentials use request headers. Broker and database credentials are mounted from private files for the selected service role. Original observations, replay packages, analysis inputs, model outputs, and backup bundles contain operational information and remain local. The source and data inventories describe the material selected for distribution. [Runtime boundaries](engineering/runtime-topology.md) · [Data sources](data-sources.md)

The separate browser demonstration may distribute an explicitly allowlisted semantic export after matched-recording and actual-case browser verification. Stable public identities, selected numerical observations, forecast summaries, simulated charging effects, and attributed map geometry belong to that new contract; original packets, runtime identities, complete jobs, and source-to-export audits remain private. A fixture cannot qualify a public case. The local candidate includes its content inventory and notices; publication still requires review of the exact bundle and destination. [Demo contract](design/browser-demo-contract.md) · [Preparation](setup/browser-demo.md)

## Deliberate local cleanup

The retention policy keeps foundation evidence, working simulation inputs, original frozen models and consumed evaluation identities, the prepared current demonstration, and active or unresolved recovery data. Completed historical recordings, backups, superseded study input copies, and diagnostics are retired after path/ownership review and concise outcome/issue summaries. Historical results without their original payloads are explicitly summary-only. This deliberate maintenance does not enable background retention, erase consumed evaluation identities, or authorize publication. See [local data retention](setup/local-data-retention.md) and [ADR 0075](adr/0075-minimal-historical-retention.md).

## Commit checks

1. Run `python scripts/privacy_guard.py` to check candidate paths and content for private material and local account identifiers.
2. Install the pinned local scanner with `python scripts/secrets.py install` if needed. The installer verifies the archive fingerprint before extracting the executable.
3. Run `python scripts/secrets.py scan` to scan a temporary candidate snapshot with Gitleaks. Detailed reports and redacted diagnostics stay under ignored `.runtime/tools/gitleaks/`.
4. Review staged changes with `git diff --cached`. The local hook runs `python scripts/privacy_guard.py --staged` and `python scripts/secrets.py scan --staged` against the exact bytes selected for the commit.

Install the hook per checkout with `git config --local core.hooksPath .githooks`. Ignore rules prevent ordinary staging of local files. Already tracked files and forced staging require explicit review, which the path guard also checks. A private path or credential finding blocks the reviewed snapshot until resolved.

The bundled Inter font is a reviewed public binary. The guard accepts its exact path and SHA-256 fingerprint, recorded with its upstream license in the [asset inventory](../apps/dashboard/src/ui/assets/README.md). A changed font or another binary still requires review. The archive verifier applies the same rule to the archived bytes.

The three [charging-story captures](media/charging-story/README.md) are reviewed public presentation assets with exact paths and SHA-256 fingerprints in the same guard. They show retained simulation values in the current interface, with credentials and machine details excluded. Changed captures require a new review and fingerprint. Original screenshots, recordings, and diagnostics remain private.

The [dashboard overview](media/dashboard/README.md) is a separately reviewed capture of the current recorded demonstration. Its exact path and SHA-256 fingerprint use the same guard; other screenshots and changed image bytes remain blocked.

## Publication snapshot and history

The public repository starts from an approved source archive and receives one initial commit on `main`. The local development repository keeps its commit history and internal tool snapshots. The final publication review checks archive contents, the new repository's sole commit, and the approved author identity. [Publication procedure](release/publication.md)

`python scripts/secrets.py scan --history` audits the local repository's Git history. Findings remain in the private scanner report. Classify them by commit and source, including internal tool snapshots, before deciding whether a credential needs revocation or a public candidate needs correction. Preserve this distinction in the private review record.

## Authorship and references

Original code, documentation, and synthetic scenarios use the [MIT license](../LICENSE). Preserve the notices required by incorporated software and public data. The reviewed OpenStreetMap subset keeps its source receipt and attribution. [Rights inventory](rights/README.md)

Detailed audit findings, account identifiers, and raw review output remain private. The public release describes measured behavior and records the applicable input and model boundaries.

## Verification of the safeguards

The privacy guard was exercised in an isolated test repository. It rejected staged secret-shaped bytes after the working file was cleaned and rejected a forced-staged private evidence path. The fixture used nonfunctional synthetic values. The source packager separately compares each archived file with its committed bytes and applies content and path checks. [Source packager](../scripts/release/package_source.py)
