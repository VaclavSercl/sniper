import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "platform/research"))
sys.path.insert(0, str(ROOT / "infra/beroun"))
import p014_capture as capture
import migrate_p014_wrapper as migration


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "ticks.jsonl"
        self.now = 1790000000000
        self.premium = {"symbol": "BTCUSDT", "time": self.now, "markPrice": "100",
                        "indexPrice": "99", "lastFundingRate": "-0.0001"}
        self.spot = {"symbol": "BTCUSDT", "bidPrice": "98", "askPrice": "99"}

    def collect(self, premium=None, spot=None):
        values = iter([self.premium if premium is None else premium,
                       self.spot if spot is None else spot])
        return capture.capture(self.output, self.now + 1000,
                               fetch=lambda url: next(values), clock=lambda: self.now / 1000)

    def test_preserves_history_and_snapshot_identity(self):
        old = b'{"t":1,"fr":0.01}\n'
        self.output.write_bytes(old)
        self.assertEqual(self.collect()["status"], "CAPTURED")
        self.assertTrue(self.output.read_bytes().startswith(old))
        row = json.loads(self.output.read_text().splitlines()[-1])
        self.assertEqual(row["fr"], -0.0001)
        self.assertEqual(row["fr_kind"], "snapshot_not_settlement")
        self.assertEqual(row["t"], self.now)
        self.assertIsNone(row["spot_time_ms"])
        self.assertNotIn("pnl", row)

    def test_expired_at_boundary_has_no_network_or_write(self):
        result = capture.capture(self.output, self.now,
                                 fetch=lambda url: self.fail("Unexpected HTTP"), clock=lambda: self.now / 1000)
        self.assertEqual(result["status"], "EXPIRED")
        self.assertFalse(self.output.exists())

    def test_expiry_during_fetch_does_not_write(self):
        times = iter([self.now / 1000, self.now / 1000 + 2])
        data = iter([self.premium, self.spot])
        result = capture.capture(self.output, self.now + 1000,
                                 fetch=lambda url: next(data), clock=lambda: next(times))
        self.assertEqual(result["status"], "EXPIRED")
        self.assertFalse(self.output.exists())

    def test_invalid_numbers_identity_and_times_do_not_write(self):
        for field, value in [("symbol", "ETHUSDT"), ("markPrice", "NaN"), ("markPrice", "Infinity"),
                             ("indexPrice", 0), ("indexPrice", True), ("lastFundingRate", {}),
                             ("time", self.now - 120001), ("time", self.now + 5001), ("time", True)]:
            with self.subTest(field=field, value=value):
                with self.assertRaises((ValueError, capture.CaptureError)):
                    self.collect(premium={**self.premium, field: value})
                self.assertFalse(self.output.exists())
        with self.assertRaises(KeyError):
            self.collect(premium={"symbol": "BTCUSDT", "time": self.now})
        for spot in [{**self.spot, "bidPrice": "101"}, {**self.spot, "symbol": "ETHUSDT"}]:
            with self.assertRaises(capture.CaptureError):
                self.collect(spot=spot)

    def test_lock_contention_and_partial_old_row_preserved(self):
        old = b'{"partial":'
        self.output.write_bytes(old)
        with self.assertRaisesRegex(capture.CaptureError, "INCOMPLETE_EXISTING_ROW"):
            self.collect()
        self.assertEqual(self.output.read_bytes(), old)
        self.output.write_bytes(b"{}\n")
        with capture.output_lock(self.output):
            with self.assertRaisesRegex(capture.CaptureError, "OUTPUT_LOCKED"):
                self.collect()
        self.assertEqual(self.output.read_bytes(), b"{}\n")

    def test_partial_write_is_rolled_back(self):
        self.output.write_bytes(b"{}\n")
        original = os.write
        calls = []
        def broken(fd, raw):
            calls.append(raw)
            if len(calls) == 2:
                return original(fd, raw[:3])
            if len(calls) == 3:
                raise OSError("fixture disk error")
            return original(fd, raw)
        with patch.object(capture.os, "write", side_effect=broken):
            with self.assertRaises(OSError):
                self.collect()
        self.assertEqual(self.output.read_bytes(), b"{}\n")

    def test_parent_missing_and_directory_output_refused(self):
        with self.assertRaises(capture.CaptureError):
            capture.append_row(self.root / "missing" / "ticks", {})
        self.output.mkdir()
        with self.assertRaises(capture.CaptureError):
            capture.append_row(self.output, {})

    @unittest.skipIf(os.name == "nt", "Symlink creation requires separate Windows capability")
    def test_symlinks_and_hardlinks_refused(self):
        target = self.root / "other"
        target.write_bytes(b"{}\n")
        self.output.symlink_to(target)
        with self.assertRaises(capture.CaptureError):
            self.collect()
        self.output.unlink()
        os.link(target, self.output)
        with self.assertRaises(capture.CaptureError):
            self.collect()
        self.assertEqual(target.read_bytes(), b"{}\n")

    def test_http_bounded_and_invalid_json(self):
        class Response(io.BytesIO):
            pass
        for raw in [b"[1,2]", b"bad json", b" " * (capture.MAX_RESPONSE + 1)]:
            with patch.object(capture.urllib.request, "urlopen", return_value=Response(raw)) as fetch:
                with self.assertRaises((ValueError, capture.CaptureError)):
                    capture.get_json(capture.PREMIUM)
                self.assertEqual(fetch.call_args.kwargs["timeout"], capture.REQUEST_TIMEOUT)

    def test_worker_surfaces_network_failure_without_exception_text(self):
        out = io.StringIO()
        with patch.object(capture, "capture", side_effect=OSError("sensitive fixture text")), contextlib.redirect_stdout(out):
            self.assertEqual(capture.main(["--worker", "--output", str(self.output)]), 1)
        self.assertNotIn("sensitive", out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["reason"], "OSError")

    def test_supervisor_timeout_and_child_failure_visible(self):
        for result in [subprocess.TimeoutExpired(["fixture"], 40), subprocess.CompletedProcess([], 7, "", "private")]:
            out = io.StringIO()
            kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
            with patch.object(capture.subprocess, "run", **kwargs), contextlib.redirect_stdout(out):
                self.assertNotEqual(capture.main(["--output", str(self.output)]), 0)
            self.assertEqual(json.loads(out.getvalue())["status"], "FAILED")
            self.assertNotIn("private", out.getvalue())

    def test_actual_supervisor_expired_cli(self):
        p = subprocess.run([sys.executable, "-B", str(Path(capture.__file__)), "--output", str(self.output),
                            "--end", "2000-01-01T00:00:00Z"], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["status"], "EXPIRED")
        self.assertFalse(self.output.exists())


@unittest.skipIf(os.name == "nt", "Deployment uses Linux symlinks and Unix file permissions")
class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / "installed"
        self.release = "a" * 40
        source = self.base / "releases" / self.release
        files = {"infra/beroun/p014_capture.sh": b"#!/bin/bash\nexit 7\n",
                 "platform/research/p014_capture.py": b"# test fixture\n"}
        for name, raw in files.items():
            p = source / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        manifest = json.dumps({"commit": self.release, "files": {k: migration.digest(v) for k, v in files.items()}}).encode()
        (source / "RELEASE.json").write_bytes(manifest)
        self.manifest = migration.digest(manifest)
        (self.base / "current").symlink_to(source)
        self.wrapper = self.root / "tick_carry.sh"
        self.old = b"#!/bin/bash\nfalse || true\n"
        self.wrapper.write_bytes(self.old)
        self.wrapper.chmod(0o750)
        for key, value in {"BASE": self.base, "WRAPPER": self.wrapper, "RECOVERY": self.root / "recovery"}.items():
            patcher = patch.object(migration, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def migrate(self, apply=False):
        return migration.migrate(self.release, migration.digest(self.old), self.manifest, apply)

    def test_preview_apply_repeat_and_recovery(self):
        self.assertEqual(self.migrate()["status"], "PREVIEW")
        self.assertFalse((self.root / "recovery").exists())
        result = self.migrate(True)
        self.assertEqual(result["status"], "APPLIED")
        self.assertEqual(Path(result["backup"]).read_bytes(), self.old)
        self.assertEqual(self.wrapper.stat().st_mode & 0o777, 0o750)
        self.assertEqual(self.migrate(True)["status"], "ALREADY_CURRENT")
        self.assertEqual(subprocess.run(["bash", str(self.wrapper)]).returncode, 7)

    def test_modified_wrapper_or_manifest_refused(self):
        self.wrapper.write_bytes(b"owner edit")
        with self.assertRaises(ValueError):
            self.migrate(True)
        self.assertEqual(self.wrapper.read_bytes(), b"owner edit")
        self.wrapper.write_bytes(self.old)
        with self.assertRaises(ValueError):
            migration.migrate(self.release, migration.digest(self.old), "0" * 64, True)
        self.assertFalse((self.root / "recovery").exists())


if __name__ == "__main__":
    unittest.main()
