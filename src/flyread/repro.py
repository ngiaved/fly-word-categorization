"""Reproducibility: seeding, checksums, run manifests, environment capture.

Nothing here changes numerical behaviour by itself; it records what is needed
to reproduce a run and makes randomness explicit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import random
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

LOGGER = logging.getLogger(__name__)

CHUNK = 1 << 20


def file_digest(path: str | os.PathLike[str], algorithm: str = "md5") -> str:
    """Streaming digest so multi-hundred-MB files stay off the heap."""
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksum(path: str | os.PathLike[str], expected: str, algorithm: str = "md5") -> str:
    """Verify ``path`` against ``expected`` and return the computed digest.

    Raises ``ChecksumMismatch`` with both digests so the failure is actionable.
    """
    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"required data file is missing: {resolved}")
    actual = file_digest(resolved, algorithm)
    if expected and actual != expected:
        raise ChecksumMismatch(
            f"checksum mismatch for {resolved}\n"
            f"  expected ({algorithm}): {expected}\n"
            f"  actual   ({algorithm}): {actual}\n"
            "The pinned FlyWire release does not match configuration; refusing to run."
        )
    return actual


class ChecksumMismatch(RuntimeError):
    """Raised when a data file does not match its pinned checksum."""


def seed_everything(seed: int) -> dict[str, Any]:
    """Seed Python, NumPy, and Brian2 from one integer.

    Returns the seeded namespaces so the manifest can record them.
    """
    seed = int(seed)
    if seed < 0:
        raise ValueError(f"seed must be non-negative, got {seed}")
    random.seed(seed)
    np.random.seed(seed % (2**32))

    brian_info: dict[str, Any] = {"available": False}
    try:
        import brian2

        brian2.seed(seed)
        brian_info = {
            "available": True,
            "version": brian2.__version__,
            # brian2.seed() returns the previously active seed, and returns
            # None on a fresh process, so record the value we asked for.
            "requested_seed": int(seed),
            "codegen_target": str(brian2.prefs.codegen.target),

        }
    except ImportError:  # pragma: no cover - brian2 is a hard dependency at runtime
        LOGGER.warning("brian2 not importable; only Python and NumPy were seeded")

    return {
        "seed": seed,
        "python_random": seed,
        "numpy_legacy_seed": seed % (2**32),
        "numpy_default_rng_seed": seed,
        "brian2": brian_info,
    }


def make_rng(seed: int, stream: str = "") -> np.random.Generator:
    """Independent generator derived from a run seed and a named stream.

    The stream name is folded into the entropy as a 32-bit value, so derived
    streams (train/test splits, weight noise, pixel shuffles) are reproducible,
    independent of one another, and independent of call ordering.
    """
    entropy: list[int] = [int(seed) % (2**32)]
    if stream:
        digest = hashlib.sha256(stream.encode("utf-8")).digest()
        entropy.append(int.from_bytes(digest[:4], "big"))
    return np.random.default_rng(np.random.SeedSequence(entropy))


def git_commit(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Best-effort git provenance. Records "dirty" instead of failing."""
    cwd = str(root or Path.cwd())
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                args, cwd=cwd, capture_output=True, text=True, timeout=10, check=True
            ).stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return ""

    commit = run("git", "rev-parse", "HEAD")
    return {
        "commit": commit or None,
        "branch": run("git", "rev-parse", "--abbrev-ref", "HEAD") or None,
        "dirty": bool(run("git", "status", "--porcelain")),
        "remote": run("git", "config", "--get", "remote.origin.url") or None,
    }


def dependency_versions() -> dict[str, str]:
    """Versions of every pinned dependency, for the manifest."""
    names = [
        "brian2", "numpy", "scipy", "pyarrow", "yaml", "PIL",
        "matplotlib", "psutil", "pytest",
    ]
    out: dict[str, str] = {}
    for name in names:
        try:
            module = __import__(name)
            out[name] = getattr(module, "__version__", "unknown")
        except ImportError:
            out[name] = "not installed"
    return out


def hardware_summary() -> dict[str, Any]:
    """Hardware description used to interpret benchmark timings."""
    info: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "python": sys.version.split()[0],
        "logical_cpus": os.cpu_count(),
    }
    try:
        import psutil

        info["physical_cpus"] = psutil.cpu_count(logical=False)
        info["total_memory_gb"] = round(psutil.virtual_memory().total / 1024**3, 2)
        process = psutil.Process()
        info["peak_rss_mb"] = round(
            process.memory_info().rss / 1024**2, 1
        )
        info["peak_rss_mb"] = max(
            info["peak_rss_mb"],
            round(process.memory_info().rss / 1024**2, 1),
        )
    except ImportError:  # pragma: no cover
        pass
    return info


@dataclass
class RunManifest:
    """Accumulates everything needed to reproduce and audit one run."""

    run_id: str
    seed: int
    config: dict[str, Any]
    seed_record: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    subcircuit: dict[str, Any] = field(default_factory=dict)
    encoding: dict[str, Any] = field(default_factory=dict)
    calibration: dict[str, Any] = field(default_factory=dict)
    benchmark: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    events: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
        LOGGER.warning("%s", message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seed": self.seed,
            "seeding": self.seed_record,
            "config": self.config,
            "data": self.data,
            "subcircuit": self.subcircuit,
            "encoding": self.encoding,
            "calibration": self.calibration,
            "benchmark": self.benchmark,
            "metrics": self.metrics,
            "events": self.events,
            "warnings": self.warnings,
            "environment": {
                "dependencies": dependency_versions(),
                "hardware": hardware_summary(),
                "git": git_commit(),
                "codegen_target": self.seed_record.get("brian2", {}).get(
                    "codegen_target"
                ),
            },
            "extra": self.extra,
        }

    def write(self, output_dir: str | os.PathLike[str]) -> Path:
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "manifest.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True, default=str)
            handle.write("\n")
        LOGGER.info("wrote manifest to %s", path)
        return path


def new_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}"


def dumps_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)