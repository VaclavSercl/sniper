#!/usr/bin/env python3
"""Preview/apply one hash-bound P014 wrapper replacement; retain recovery bytes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

BASE = Path("/opt/sniper")
WRAPPER = Path("/home/wwwenda/.hermes/scripts/tick_carry.sh")
RECOVERY = Path("/home/wwwenda/sniper-operations/p014")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def no_links(path):
    for entry in (path, *path.parents):
        if entry.is_symlink():
            raise ValueError("Symlink in managed path")


def atomic(path, raw, mode):
    no_links(path)
    fd, name = tempfile.mkstemp(prefix=".p014-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def migrate(release, expected_old, expected_manifest, apply=False):
    if not re.fullmatch("[0-9a-f]{40,64}", release):
        raise ValueError("Invalid release")
    if not all(re.fullmatch("[0-9a-f]{64}", x) for x in (expected_old, expected_manifest)):
        raise ValueError("Invalid expected digest")
    source = BASE / "releases" / release
    no_links(source)
    current = BASE / "current"
    if not current.is_symlink() or current.readlink() != source:
        raise ValueError("Expected release is not active")
    manifest_path = source / "RELEASE.json"
    no_links(manifest_path)
    manifest = manifest_path.read_bytes()
    if digest(manifest) != expected_manifest:
        raise ValueError("Unapproved manifest")
    metadata = json.loads(manifest)
    if metadata["commit"] != release:
        raise ValueError("Wrong commit")
    for name in ("infra/beroun/p014_capture.sh", "platform/research/p014_capture.py"):
        path = source / name
        no_links(path)
        if digest(path.read_bytes()) != metadata["files"][name]:
            raise ValueError("Changed release source")
    replacement = (source / "infra/beroun/p014_capture.sh").read_bytes()
    no_links(WRAPPER)
    original = WRAPPER.read_bytes()
    info = WRAPPER.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Unsafe existing wrapper")
    record = {"release": release, "wrapper": str(WRAPPER), "old_sha256": expected_old,
              "new_sha256": digest(replacement), "manifest_sha256": expected_manifest,
              "status": "PREVIEW", "scheduler_changed": False, "data_changed": False}
    if digest(original) == digest(replacement):
        record["status"] = "ALREADY_CURRENT"
        return record
    if digest(original) != expected_old:
        raise ValueError("Existing wrapper changed")
    if not apply:
        return record
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise ValueError("Run as the existing wrapper owner")
    no_links(RECOVERY)
    RECOVERY.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(RECOVERY.stat().st_mode) & 0o077:
        raise ValueError("Recovery directory must be private")
    lock = RECOVERY / "migration.lock"
    no_links(lock)
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        operation = RECOVERY / uuid.uuid4().hex
        operation.mkdir(mode=0o700)
        backup = operation / "tick_carry.sh.original"
        atomic(backup, original, 0o600)
        if digest(backup.read_bytes()) != expected_old:
            raise ValueError("Backup verification failed")
        record.update(status="STARTED", backup=str(backup), old_mode=stat.S_IMODE(info.st_mode))
        journal = operation / "operation.json"
        atomic(journal, (json.dumps(record, indent=2) + "\n").encode(), 0o600)
        no_links(WRAPPER)
        if current.readlink() != source or digest(WRAPPER.read_bytes()) != expected_old:
            raise ValueError("Concurrent deployment or wrapper change")
        atomic(WRAPPER, replacement, stat.S_IMODE(info.st_mode))
        if digest(WRAPPER.read_bytes()) != digest(replacement):
            raise ValueError("Replacement verification failed; inspect backup")
        record["status"] = "APPLIED"
        atomic(journal, (json.dumps(record, indent=2) + "\n").encode(), 0o600)
        return record
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--expected-old-sha256", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(args.release, args.expected_old_sha256, args.manifest_sha256, args.apply), indent=2))


if __name__ == "__main__":
    main()
