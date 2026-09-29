#!/usr/bin/env python3
"""Bounded public P014 observations; no settlement accounting or trading."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import urllib.request

END = "2026-10-06T19:00:00+00:00"
SYMBOL = "BTCUSDT"
PREMIUM = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
SPOT = "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT"
MAX_RESPONSE = 65536
REQUEST_TIMEOUT = 15
RUN_TIMEOUT = 40


class CaptureError(Exception):
    pass


def end_ms(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("End must be UTC")
    return int(result.timestamp() * 1000)


def get_json(url):
    if url not in (PREMIUM, SPOT):
        raise CaptureError("UNAPPROVED_ENDPOINT")
    req = urllib.request.Request(url, headers={"User-Agent": "sniper-p014/2"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise CaptureError("RESPONSE_TOO_LARGE")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise CaptureError("INVALID_RESPONSE")
    return data


def number(data, key, positive=False):
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise CaptureError("INVALID_NUMBER")
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise CaptureError("INVALID_NUMBER")
    return value


def observation(premium, spot, started, received):
    if premium.get("symbol") != SYMBOL or spot.get("symbol") != SYMBOL:
        raise CaptureError("WRONG_INSTRUMENT")
    source_time = premium.get("time")
    if type(source_time) is not int or not received - 120000 <= source_time <= received + 5000:
        raise CaptureError("STALE_OR_FUTURE_MARK")
    if received < started or received - started > RUN_TIMEOUT * 1000:
        raise CaptureError("INVALID_CAPTURE_WINDOW")
    row = {
        "t": received, "mark": number(premium, "markPrice", True),
        "index": number(premium, "indexPrice", True),
        "fr": number(premium, "lastFundingRate"),
        "bid": number(spot, "bidPrice", True), "ask": number(spot, "askPrice", True),
        "schema_version": 2, "symbol": SYMBOL, "source": "binance_public",
        "started_ms": started, "premium_time_ms": source_time,
        "spot_time_ms": None, "fr_kind": "snapshot_not_settlement",
    }
    if row["bid"] > row["ask"]:
        raise CaptureError("CROSSED_QUOTE")
    return row


def safe_path(path):
    path = Path(path).absolute()
    for entry in (path, *path.parents):
        if entry.is_symlink():
            raise CaptureError("SYMLINK_PATH")
    if not path.parent.is_dir():
        raise CaptureError("OUTPUT_DIRECTORY_MISSING")
    return path


@contextmanager
def output_lock(path):
    lock = safe_path(path.with_name(path.name + ".lock"))
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise CaptureError("OUTPUT_LOCKED") from None
    try:
        os.write(fd, (str(os.getpid()) + "\n").encode())
        os.fsync(fd)
        yield
    finally:
        os.close(fd)
        lock.unlink()


def append_row(path, row):
    path = safe_path(path)
    raw = (json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n").encode()
    with output_lock(path):
        # Recheck after lock acquisition. Locks protect cooperating writers only.
        safe_path(path)
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
            raise CaptureError("UNSAFE_OUTPUT")
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise CaptureError("UNSAFE_OUTPUT")
            size = os.lseek(fd, 0, os.SEEK_END)
            if size:
                os.lseek(fd, -1, os.SEEK_END)
                if os.read(fd, 1) != b"\n":
                    raise CaptureError("INCOMPLETE_EXISTING_ROW")
            os.lseek(fd, 0, os.SEEK_END)
            try:
                offset = 0
                while offset < len(raw):
                    written = os.write(fd, raw[offset:])
                    if written <= 0:
                        raise OSError("Incomplete append")
                    offset += written
                os.fsync(fd)
            except BaseException:
                # Only restore the pre-append length while owning the writer lock.
                os.ftruncate(fd, size)
                os.fsync(fd)
                raise
        finally:
            os.close(fd)


def capture(output, end, fetch=get_json, clock=time.time):
    started = int(clock() * 1000)
    if started >= end:
        return {"status": "EXPIRED", "end_ms": end, "written": False}
    # No directory creation or adoption of unexpected paths.
    safe_path(output)
    premium, spot = fetch(PREMIUM), fetch(SPOT)
    received = int(clock() * 1000)
    if received >= end:
        return {"status": "EXPIRED", "end_ms": end, "written": False}
    row = observation(premium, spot, started, received)
    append_row(output, row)
    return {"status": "CAPTURED", "t": row["t"], "written": True,
            "symbol": SYMBOL, "qualification": "RAW_OBSERVATION_ONLY"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--end", default=END)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        end = end_ms(args.end)
        if not args.worker:
            # A separate bounded worker also covers DNS stalls and slow body reads.
            command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
                       "--output", str(args.output), "--end", args.end]
            result = subprocess.run(command, capture_output=True, text=True, timeout=RUN_TIMEOUT)
            if result.stdout:
                print(result.stdout, end="")
            if result.returncode and not result.stdout:
                print(json.dumps({"status": "FAILED", "reason": "WORKER_FAILED"}))
            return result.returncode if result.returncode >= 0 else 1
        result = capture(args.output, end)
        print(json.dumps(result, allow_nan=False))
        return 0
    except subprocess.TimeoutExpired:
        # The child may have written before timeout: do not promise no side effect.
        print(json.dumps({"status": "FAILED", "reason": "RUN_TIMEOUT", "write_outcome": "UNKNOWN"}))
        return 1
    except (OSError, ValueError, KeyError, TypeError, CaptureError) as exc:
        reason = str(exc) if isinstance(exc, CaptureError) else type(exc).__name__
        print(json.dumps({"status": "FAILED", "reason": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
