# Incorporated software and rights inventory

Original DriveTwin code, documentation, and synthetic scenarios use the [MIT license](../../LICENSE). This inventory records the terms and notice locations for incorporated software and data. Package locks and image manifests identify the exact reviewed dependencies.

## Exact language-package inventories

| Group | Inventory | Scope |
|---|---|---|
| Python application/checks | [Python declarations](python-dependencies.json), 47 distributions | Locked application and development dependencies, including the Windows-only Colorama test dependency |
| Simulation | [SUMO declarations](simulation-dependencies.json), 4 distributions | eclipse-sumo, traci, sumolib, sumo-data 1.28.0 |
| TypeScript contracts | [npm declarations](typescript-dependencies.json), 8 packages | Locked runtime validators and compiler dependencies |
| Browser qualification | [Browser declarations](browser-dependencies.json), 3 packages | Playwright package declarations; downloaded browser/OS components remain separate |
| Dashboard | [Dashboard declarations](dashboard-dependencies.json), 51 locked package versions | Browser application and build tools |
| Terrain preparation | [Terrain declarations](terrain-preparation-dependencies.json), 7 distributions | Optional offline importer; separately pinned Ubuntu CPython 3.12 wheels, excluded from numerical workers |
| OSM extraction and atlas context | [OSM preparation declarations](osm-preparation-dependencies.json), 6 distributions | Optional Windows CPython 3.13 tools, pinned separately and excluded from application images and numerical workers |

The source release review matched all 51 third-party Python names and versions in `uv.lock` and all 62 names and versions across the three JavaScript locks. The Python lock marks Colorama as Windows-only, and the versioned upstream notice supplies its license terms. [Colorama 0.4.6 notice](https://raw.githubusercontent.com/tartley/colorama/0.4.6/LICENSE.txt)

Entries record declared metadata and available notice paths, without package author emails/account data. Names such as “BSD License” or “OSI Approved” are broad classifiers; they do not establish an exact license by themselves. Read the versioned notice for release packaging. Paho 2.1.0's installed notice explicitly states dual Eclipse Public License 2.0 / Eclipse Distribution License 1.0 terms. [Versioned upstream notice](https://github.com/eclipse-paho/paho.mqtt.python/blob/v2.1.0/LICENSE.txt)

P1-A adds the development-only mypy checker and its locked helpers: mypy 2.4.1, mypy_extensions 1.2.0, librt 0.16.0 and ast_serialize 0.13.1 declare MIT; pathspec 1.1.1 declares MPL-2.0. Their versioned metadata/notice paths are recorded in the Python inventory. Ordinary backend images exclude the development group; the optional simulator group reuses the separately inventoried four SUMO packages. These declarations do not replace review of an eventual distributed artifact.

The inventory exposed optional `rfc3987` 1.3.8, whose metadata declares GPLv3-or-later. Root dependencies now select jsonschema's supported `format-nongpl` extra. The lock removes that parser and adds the documented alternative dependencies; jsonschema stays 4.26.0. Existing date/time/UUID validation remains enabled and is checked by shared cases and affected service regressions. This change does not claim that the entire toolchain is GPL-free or universally compatible with any future project license. [Official format-extra documentation](https://python-jsonschema.readthedocs.io/en/stable/validate/#validating-formats)

Psycopg/its binary package declare LGPL-3.0-only; fqdn declares MPL-2.0; SUMO packages declare EPL-2.0 OR GPL-2.0-or-later. Retain their notices and review distribution obligations for the actual package/image delivered. Binary wheels may bundle libraries with additional terms. [Psycopg binary package documentation](https://www.psycopg.org/psycopg3/docs/basic/install.html#binary-installation)

## Tool and container boundary

| Component | Primary rights source / required follow-up |
|---|---|
| Python runtime | [PSF license](https://docs.python.org/3/license.html); runtime and bundled notices travel with distributed builds |
| Node runtime/npm | [Versioned Node license](https://github.com/nodejs/node/blob/v24.22.2/LICENSE); bundled dependencies have separate notices |
| uv | [Upstream licenses](https://github.com/astral-sh/uv); MIT/Apache-2.0 declarations, preserve actual version's notices |
| PostgreSQL | [PostgreSQL license](https://www.postgresql.org/about/licence/); official image contains OS components too |
| Mosquitto | [Upstream license](https://mosquitto.org/license/); preserve selected version's Eclipse license notices |
| SUMO | [Upstream licensing](https://sumo.dlr.de/docs/FAQ.html#what_license_is_sumo_published_under); exact wheel metadata is inventoried above |
| Docker Engine/Compose/containerd | [Docker Engine source](https://github.com/moby/moby), [Compose source](https://github.com/docker/compose), [containerd source](https://github.com/containerd/containerd); Apache-2.0 project declarations do not replace bundled dependency notices |
| Git/Gitleaks | [Git copying terms](https://github.com/git/git/blob/master/COPYING), [Gitleaks license](https://github.com/gitleaks/gitleaks/blob/master/LICENSE); tools installed locally, not vendored |
| Debian/Ubuntu runtime libraries | Exact package revisions in locked sources/qualification inventories; preserve `/usr/share/doc/*/copyright` and audit bundled/native dependencies before image distribution |
| Chromium/Edge | Browser-specific terms and bundled notices; browser testing does not grant a right to redistribute a browser binary |

Public source and image manifests identify the exact inputs. Installed-package lists already accompany SUMO qualification. No image/browser export is performed by P0-06. A software bill of materials (SBOM) is a machine-readable list of components; a complete release SBOM, exact notices, source-offer obligations where applicable, and findings/remediation belong to P7's distribution review.

## Project license and publication

The first product dashboard adds React/React DOM 19.3.1 and locked build/type/format tools. [Dashboard dependency inventory](dashboard-dependencies.json) records exact package versions, declared terms and installed notice filenames without personal package-author metadata. The inventory records declared terms and notice locations. Preserve React/bundler-generated license notices and review native build-tool dependencies before distributing images or bundles. No external UI imagery, web fonts or hosted map tiles are incorporated by C1.

C2 adds Leaflet 1.9.4 (declared BSD-2-Clause), its 1.9.21 TypeScript definitions and GeoJSON 7946.0.16 definitions (declared MIT); exact entries/notices are in the same inventory. Preserve their actual notices in distributed artifacts. The locally derived road/route context retains OpenStreetMap attribution and ODbL review obligations. Hypothetical stations and mission definitions are separate assumptions. No external tile service is used, and no map or dataset is published by this checkpoint.

The maintainer selected MIT for original DriveTwin work. The root [LICENSE](../../LICENSE) contains the reuse terms. The reviewed OpenStreetMap subset retains ODbL terms and contributor attribution. The source publication review checks the selected archive, notice files, dependency declarations, and privacy results. [Publication procedure](../release/publication.md)

Regenerate declared package inventories with `scripts/collect_inventory.py` in the locked environment or against the exact npm lock. Keep raw diagnostics/private source material outside Git. Unknown/new declarations require review before incorporation/distribution; do not replace them with a guessed SPDX identifier. SPDX is a standardized vocabulary for license names.

## Dashboard preview assets

The dashboard and interactive design preview bundle Inter under the SIL Open Font License 1.1. Its exact upstream license is included in source and emitted in both builds. [Asset provenance and fingerprint](../../apps/dashboard/src/ui/assets/README.md). The icons and DriveTwin symbol are original SVG artwork. The local street map retains the reviewed OpenStreetMap attribution.

The README adds three reviewed dashboard captures and an original charge-history figure. Their [media inventory](../media/charging-story/README.md) identifies the retained SUMO case, the current-interface reconstruction, reviewed image fingerprints, and applicable artwork and font notices. Raw recordings and original live captures remain private.

The [recorded dashboard overview](../media/dashboard/README.md) adds one reviewed capture with the same software, font and map notices. The [geographic error chart](../engineering/figures/geographic-forecast-errors.svg) is an original Matplotlib figure built from the published regression summary by [its generator](../../scripts/build_geographic_result_figure.py); it embeds no external imagery or fonts.
