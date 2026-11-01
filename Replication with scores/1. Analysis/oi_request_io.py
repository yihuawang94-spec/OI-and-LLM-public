# -*- coding: utf-8 -*-
"""Shared file, lock and request utilities for the fresh OI scoring workflow."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any
import uuid
import pandas as pd


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode("utf-8")).hexdigest()

def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()

def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path.name}.")
    return value

def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

def append_record(stream: Any, record: dict) -> None:
    stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    stream.flush()
    os.fsync(stream.fileno())

def committed_records(path: Path, repair_tail: bool = False):
    """Only an unfinished FINAL line may be discarded; never change raw files."""
    if not path.exists():
        return
    with path.open("r+b" if repair_tail else "rb") as stream:
        line_number = 0
        while True:
            position = stream.tell()
            line = stream.readline()
            if not line:
                break
            line_number += 1
            if not line.endswith(b"\n"):
                if repair_tail:
                    stream.truncate(position)
                    stream.flush()
                    os.fsync(stream.fileno())
                    print("Removed an unfinished final response-log line; its request remains pending.", flush=True)
                else:
                    print(f"Ignoring an unfinished final line in {path.name}; file left unchanged.", flush=True)
                break
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("Expected object")
            except (ValueError, UnicodeError) as exc:
                raise RuntimeError(f"Invalid committed JSON at {path.name}, line {line_number}; file unchanged.") from exc
            yield line_number, record

class InvalidReply(ValueError):
    pass

class ProtocolChanged(RuntimeError):
    pass

def record_model(record: dict) -> str | None:
    top = record.get("model_version")
    nested = (record.get("raw_response") or {}).get("model_version")
    if top and nested and top != nested:
        raise ProtocolChanged("Saved model version disagrees with the raw API response.")
    return top or nested

def atomic_dta(frame: pd.DataFrame, path: Path, label: str, variable_labels: dict | None = None) -> None:
    if any(len(column) > 32 for column in frame):
        raise ValueError("An output variable exceeds Stata's 32-character limit.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".tmp.dta")
    try:
        frame.to_stata(temporary, write_index=False, version=118, data_label=label,
                       variable_labels=variable_labels or {})
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

def _process_alive(pid: int) -> bool | None:
    if type(pid) is not int or pid <= 0:
        return None
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            state = kernel.WaitForSingleObject(handle, 0)
            return False if state == 0 else True if state == 258 else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None
    return True

@contextmanager
def run_lock(path: Path):
    contents = (json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                            "started_utc": datetime.now(timezone.utc).isoformat(), "token": uuid.uuid4().hex}) + "\n").encode()
    def create(destination):
        with destination.open("xb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    try:
        create(path)
    except FileExistsError:
        guard = path.with_name(path.name + ".recovery")
        try:
            create(guard)
        except FileExistsError:
            raise RuntimeError(f"Lock recovery is already active or unverified: {guard}.") from None
        try:
            old_bytes = path.read_bytes() if path.exists() else None
            if old_bytes is not None:
                try:
                    owner = json.loads(old_bytes)
                except (ValueError, UnicodeError):
                    owner = {}
                if not isinstance(owner, dict) or owner.get("host") != socket.gethostname() or _process_alive(owner.get("pid")) is not False:
                    raise RuntimeError(f"Another run may still be active. Check the owner of {path} before removing it.")
                if path.read_bytes() != old_bytes:
                    raise RuntimeError("Lock changed during stale-owner checking.")
                path.unlink()
                print("Recovered a lock left by a stopped local process.", flush=True)
            create(path)
        finally:
            if guard.exists() and guard.read_bytes() == contents:
                guard.unlink()
    try:
        yield
    finally:
        if path.exists() and path.read_bytes() == contents:
            path.unlink()

class Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, value):
        for stream in self.streams:
            stream.write(value)
            stream.flush()
        return len(value)
    def flush(self):
        for stream in self.streams:
            stream.flush()
    def isatty(self):
        return False

@contextmanager
def console_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        previous = sys.stdout
        sys.stdout = Tee(previous, log)
        try:
            print(f"\nScoring run started {datetime.now(timezone.utc).isoformat()}", flush=True)
            yield
        finally:
            sys.stdout = previous

@contextmanager
def request_progress(description: str, interval: int):
    start, stop = time.monotonic(), threading.Event()
    print(f"{description}: requesting scores.", flush=True)
    def heartbeat():
        while not stop.wait(interval):
            print(f"{description}: still waiting after {time.monotonic() - start:.0f}s; HTTP timeout is active.", flush=True)
    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=1)

def import_google_sdk(version: str):
    try:
        installed = importlib.metadata.version("google-genai")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ImportError(f"Install google-genai=={version} in the Spyder Python environment.") from exc
    if installed != version:
        print(f"SDK version {installed}; original study used {version}. This version is recorded in the run log.")
    # Match the original working httpx path on Windows, keeping TLS checks on.
    absent = object()
    previous = sys.modules.get("aiohttp", absent)
    sys.modules["aiohttp"] = None
    try:
        from google import genai
        from google.genai import types
    finally:
        if previous is absent:
            sys.modules.pop("aiohttp", None)
        else:
            sys.modules["aiohttp"] = previous
    return genai, types

def error_code(error: Exception) -> int | None:
    try:
        return int(getattr(error, "code", None))
    except (ValueError, TypeError):
        return None
