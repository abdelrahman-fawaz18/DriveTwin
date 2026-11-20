# Contributing

Use the [platform setup](setup/windows-wsl.md) and [engineering checks](setup/engineering-checks.md). Windows PowerShell commands run from the project root; Linux commands go through `python scripts/linux.py -- COMMAND` in Ubuntu. Keep source, tests and reproducible inputs in their existing named directories.

Before implementation, read the [current scope](../SCOPE.md), [architecture](architecture.md), relevant [decisions](adr/) and [capability evidence](release/capabilities.md). Preserve frozen contracts, raw observation semantics, consumed evaluation identities and the operator/executor/observer/replay/writer boundaries. A changed forecast needs new versioned evidence rather than a relabeled reserved result. [Compatibility](compatibility.md)

Use focused offline checks for the affected behavior. Run the shared checks when changed dependencies or unresolved failures warrant them. Live workloads need their applicable authorization and existing host safeguards. Do not use cleanup, documentation work or an ordinary test fix to authorize a fleet run or change host settings.

Save a reviewed local commit after checking the exact staged bytes:

```powershell
git status --short
python scripts/privacy_guard.py
python scripts/secrets.py scan
git add -- <reviewed-paths>
git diff --cached
python scripts/privacy_guard.py --staged
python scripts/secrets.py scan --staged
git commit -m "Describe the resulting behavior"
```

Replace `<reviewed-paths>` with the intended source files. Never force-stage private paths. Keep credentials, machine settings, recordings, diagnostic reports, laboratory source data, private fitted artifacts and backups ignored. Explain changes with their tested behavior, evidence scope and material limitations. Preserve required attribution and licenses. [Privacy](privacy.md) · [Rights inventory](rights/README.md)

A local commit or private source archive does not publish anything. The first public repository must come from an explicitly approved snapshot with one initial commit on `main`, while development history stays local. Remote configuration, upload and hosting changes require separate approval of the exact result and destination. [Publication procedure](release/publication.md)
