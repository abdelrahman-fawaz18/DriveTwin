"""Exercise publication guards using constructed, nonfunctional example values."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "privacy_guard", Path(__file__).resolve().parents[2] / "scripts/privacy_guard.py"
)
guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guard)


def test_private_paths_and_public_template():
    for name in (
        "evidence/result.json",
        ".private/notes.md",
        "docs/handoffs/note.md",
        "environments/dev.toml",
    ):
        assert guard.private_path(name)
    assert not guard.private_path("environments/dev.example.toml")
    assert not guard.private_path("docs/verification/summary.json")


def test_detects_secrets_without_echoing_them():
    token = "ghp_" + "A" * 36
    assert guard.inspect_content(token.encode()) == ["github-token"]
    assert guard.inspect_content(("AKIA" + "A" * 16).encode()) == ["aws-access-key"]
    assert guard.inspect_content(("-----BEGIN " + "PRIVATE KEY-----").encode()) == ["private-key"]


def test_local_identity_and_home_paths():
    account = "test" + "owner"
    assert guard.inspect_content(f"/home/{account}/repo".encode()) == ["personal-home-path"]
    assert guard.inspect_content(account.encode(), [account]) == ["local-account-identity"]
    assert guard.inspect_content(b"/home/YOUR_LINUX_USER/repo") == []
    assert guard.inspect_content(b"C:/Users/YOUR_WINDOWS_USER/repo") == []
    assert guard.inspect_content(b"example@local.invalid") == []


def test_binary_and_large_files_need_review():
    assert guard.inspect_content(b"\x00") == ["binary-file-needs-review"]
    assert guard.inspect_content(b"\xff") == ["binary-file-needs-review"]
    assert guard.inspect_content(b"x" * 2_000_001) == ["oversized-file-needs-review"]


def test_reviewed_font_requires_exact_path_and_bytes():
    name = "apps/dashboard/src/ui/assets/InterVariable.woff2"
    data = (guard.ROOT / name).read_bytes()
    assert guard.inspect_content(data, path=name) == []
    assert guard.inspect_content(data, path="unreviewed.woff2") == ["binary-file-needs-review"]
    assert guard.inspect_content(data + b"changed", path=name) == [
        "reviewed-asset-fingerprint-mismatch"
    ]
    assert guard.inspect_content(b"unreviewed replacement", path=name) == [
        "reviewed-asset-fingerprint-mismatch"
    ]
    assert (guard.ROOT / name).with_name("Inter-LICENSE.txt").is_file()


def test_reviewed_dashboard_captures_keep_exact_path_and_replacement_guards():
    paths = (
        "docs/media/charging-story/01-recommendation.png",
        "docs/media/charging-story/02-approved.png",
        "docs/media/charging-story/03-measured.png",
        "docs/media/dashboard/overview.png",
    )
    for path in paths:
        data = (guard.ROOT / path).read_bytes()
        assert guard.inspect_content(data, path=path) == []
        assert guard.inspect_content(data, path="unreviewed.png") == ["binary-file-needs-review"]
        assert guard.inspect_content(data + b"changed", path=path) == [
            "reviewed-asset-fingerprint-mismatch"
        ]
