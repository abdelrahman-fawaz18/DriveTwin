"""Inspect candidate/index content without printing potentially private matches."""

import argparse
import hashlib
import os
import re
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOTS = {".private", "evidence", ".runtime", ".git"}
PRIVATE_DOCS = {"handoffs", "migration", "references"}
RULES = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "credential-url": re.compile(r"\b[a-z]+://[^\s/:]+:[^\s/@]+@", re.I),
}
HOME_PATH = re.compile(r"(?:[A-Z]:[/\\]Users[/\\]|/home/)([A-Za-z0-9_.-]+)", re.I)
PLACEHOLDERS = {"YOUR_WINDOWS_USER", "YOUR_LINUX_USER", "USER", "USERNAME"}
# Exact reviewed assets with their source and notice records; replacements need review.
REVIEWED_PUBLIC_BINARIES = {
    "apps/dashboard/src/ui/assets/InterVariable.woff2": (
        "693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3"
    ),
    # Credential-free recorded-case captures: docs/media/charging-story/README.md.
    "docs/media/charging-story/01-recommendation.png": (
        "9e39df8f1f74fa0e073add27ae7c4d90562e0d1ddfeffedee4c2465a9464d68a"
    ),
    "docs/media/charging-story/02-approved.png": (
        "2706cec9aa973e67982d34f76d0570014b6d6b62f70797478678d5d7b076e271"
    ),
    "docs/media/charging-story/03-measured.png": (
        "15c2e895e942b0bee1c3b614b145d34ae9b023738168c473e4e251883b1470a5"
    ),
    # Current recorded dashboard: docs/media/dashboard/README.md.
    "docs/media/dashboard/overview.png": (
        "a4d3f9077af18d0a98f019f0897d083109843ec258cd8409316d888d587337be"
    ),
}


def private_path(name):
    parts = PurePosixPath(name).parts
    return bool(parts) and (
        parts[0] in PRIVATE_ROOTS
        or (len(parts) > 1 and parts[0] == "docs" and parts[1] in PRIVATE_DOCS)
        or name == "environments/dev.toml"
    )


def inspect_content(data, identities=(), *, path=None):
    if len(data) > 2_000_000:
        return ["oversized-file-needs-review"]
    if path in REVIEWED_PUBLIC_BINARIES:
        if hashlib.sha256(data).hexdigest() == REVIEWED_PUBLIC_BINARIES[path]:
            return []
        return ["reviewed-asset-fingerprint-mismatch"]
    try:
        content = data.decode("utf-8-sig")
    except UnicodeError:
        return ["binary-file-needs-review"]
    if "\x00" in content:
        return ["binary-file-needs-review"]
    issues = [name for name, pattern in RULES.items() if pattern.search(content)]
    if any(match[1] not in PLACEHOLDERS for match in HOME_PATH.finditer(content)):
        issues.append("personal-home-path")
    folded = content.casefold()
    for identity in identities:
        # Exact standalone local identity values; never include them in output.
        if len(identity) >= 4 and re.search(
            r"(?<![\w.-])" + re.escape(identity.casefold()) + r"(?![\w.-])", folded
        ):
            issues.append("local-account-identity")
            break
    return issues


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=check).stdout


def candidates(staged=False, tracked=False):
    if staged:
        args = ("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    elif tracked:
        args = ("ls-files", "-z")
    else:
        args = ("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    return sorted(set(git(*args).decode("utf-8").strip("\0").split("\0")) - {""})


def local_identities():
    values = {os.environ.get("USERNAME", "")}
    for key in ("user.name", "user.email"):
        values.add(git("config", "--global", "--get", key, check=False).decode().strip())
    config = ROOT / "environments/dev.toml"
    if config.exists():
        values.add(tomllib.loads(config.read_text(encoding="utf-8"))["linux"]["user"])
    return values - {"", *PLACEHOLDERS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--tracked", action="store_true")
    args = parser.parse_args()
    identities = local_identities()
    failures = []
    names = candidates(args.staged, args.tracked)
    for name in names:
        path = ROOT / name
        ignored = (
            subprocess.run(
                ["git", "check-ignore", "--no-index", "-q", "--", name], cwd=ROOT
            ).returncode
            == 0
        )
        if private_path(name) or ignored:
            failures.append((name, "private-or-ignored-path"))
            continue
        if not path.resolve().is_relative_to(ROOT) or path.is_symlink():
            failures.append((name, "symlink-needs-review"))
            continue
        if not args.staged and not path.exists():
            continue
        data = git("show", ":" + name) if args.staged else path.read_bytes()
        failures.extend((name, rule) for rule in inspect_content(data, identities, path=name))
    for name, rule in failures:
        print(f"BLOCKED {name}: {rule}")
    print(f"Privacy guard: {len(names)} files checked; {len(failures)} findings.")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
