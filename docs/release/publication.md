# Public source release procedure

The public repository will start with one commit on `main`, containing the source snapshot approved by the maintainer. The development repository retains its local history. Publication is a separate authorized action after review of the final snapshot.

## Prepare the reviewed snapshot

1. Complete the dashboard presentation, media, and README review. Keep the stated results tied to their evaluation scopes.
2. Commit the approved source locally. Run the candidate privacy and secret checks, review the selected files and author metadata, and verify documentation links against the files included in the snapshot.
3. Build a private source archive with `python scripts/release/package_source.py --name approved-release`. Use a new label when a previous archive already exists. Review the archive and its manifest, including the license, source inventory, and excluded local data.
4. Obtain the maintainer's explicit approval of that exact snapshot and the intended GitHub repository before publication.

## Create the publication repository

After approval, extract the reviewed source archive into a separately approved publication directory. Initialize a new Git repository with `git init -b main`, set the approved author identity, and create one initial commit from the reviewed files. Confirm that the branch is `main` and `git rev-list --count HEAD` returns `1`. Inspect the committed tree and rerun privacy and secret scans there.

Connect only this new repository to the approved GitHub destination and push `main`. The source archive carries tracked files and excludes the development repository's `.git` metadata. The archive's manifest remains the local record linking the published snapshot to its reviewed development revision.

## Source and data rights

Original DriveTwin code, documentation, and synthetic scenarios use the [MIT license](../../LICENSE). The reviewed OpenStreetMap subset retains contributor credit, its source receipt, and Open Database License 1.0 terms. Dependency notices remain associated with their respective packages. [Rights inventory](../rights/README.md) · [Data sources](../data-sources.md)

Credentials, machine configuration, raw recordings, diagnostic reports, laboratory source data, and local backups stay in ignored storage. The publication review covers the contents of the source archive and the metadata of the new initial commit. [Privacy policy](../privacy.md) · [Source packaging](source-package.md)
