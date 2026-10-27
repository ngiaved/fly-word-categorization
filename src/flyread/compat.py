"""Runtime toolchain compatibility helpers.

Brian2's compiled code paths (the ``cython`` codegen target) need a working
C++ compiler. On some machines the system toolchain is broken in a way that
only shows up at Brian2-run time, e.g. recent macOS Command Line Tools
shipping ``/Library/Developer/CommandLineTools/usr/include/c++/v1`` without
the umbrella C++ headers (``ios``, ``vector``, ...) while complete copies live
inside the SDKs. Compiling anything that includes ``<ios>`` then fails with
``fatal error: 'ios' file not found``.

This module:

* self-heals that situation on macOS by pointing ``CPLUS_INCLUDE_PATH`` at a
  complete libc++ header directory when one is available, and
* probes whether a minimal ``<ios>`` C++ program compiles, so callers can
  fall back to the pure-numpy codegen target (slow, but always works) when no
  working compiler exists.

Probing happens at most once per process.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
from typing import List

LOGGER = logging.getLogger(__name__)

#: Program that exercises exactly what Brian2's generated C++ needs.
_PROBE_PROGRAM = """\
#include <ios>
#include <vector>

int main() {
    std::vector<int> values;
    values.push_back(1);
    std::ios::sync_with_stdio(true);
    return values.empty() ? 1 : 0;
}
"""

_MACOS_SDK_ROOT = "/Library/Developer/CommandLineTools/SDKs"
_XCODE_SDK_ROOT = (
    "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform"
    "/Developer/SDKs"
)

#: Cached probe result; ``None`` means "not probed yet".
_CXX_COMPILER_OK: bool | None = None


def _candidate_cxx_dirs() -> List[str]:
    """Complete libc++ ``c++/v1`` directories, best first (macOS only)."""
    if os.name != "posix" or not os.path.isdir("/System/Library"):
        return []
    candidates: List[str] = []
    default_sdk = os.path.realpath(os.path.join(_MACOS_SDK_ROOT, "MacOSX.sdk"))
    for root in (_MACOS_SDK_ROOT, _XCODE_SDK_ROOT):
        if not os.path.isdir(root):
            continue
        sdks = [default_sdk] if os.path.isdir(default_sdk) else []
        for name in sorted(os.listdir(root), reverse=True):
            sdk = os.path.join(root, name)
            if sdk not in sdks and os.path.isdir(sdk):
                sdks.append(sdk)
        for sdk in sdks:
            cxx_dir = os.path.join(sdk, "usr", "include", "c++", "v1")
            if cxx_dir not in candidates and os.path.isfile(os.path.join(cxx_dir, "ios")):
                candidates.append(cxx_dir)
    return candidates


def heal_cxx_include_path() -> bool:
    """Prepend a usable libc++ header directory to ``CPLUS_INCLUDE_PATH``.

    Returns ``True`` when the environment was changed. Removing the SDK copies
    of the C++ umbrella headers broke ``clang++`` on this class of macOS
    installs; the compiler still searches ``CPLUS_INCLUDE_PATH``, so pointing
    it at an intact SDK copy fixes Cython/C++ codegen system-wide without
    touching the read-only toolchain.
    """
    if not _candidate_cxx_dirs():
        return False
    current = os.environ.get("CPLUS_INCLUDE_PATH", "")
    have = {part for part in current.split(os.pathsep) if part}
    for candidate in _candidate_cxx_dirs():
        if candidate in have:
            return False
        os.environ["CPLUS_INCLUDE_PATH"] = os.pathsep.join([candidate] + ([current] if current else []))
        LOGGER.info(
            "toolchain repair: CPLUS_INCLUDE_PATH -> %s (toolchain c++/v1 is "
            "missing its umbrella headers)",
            candidate,
        )
        return True
    return False


def probe_cxx_compiler(timeout: float = 30.0) -> bool:
    """Compile a tiny ``<ios>``-including C++ program; cache the answer."""
    global _CXX_COMPILER_OK
    if _CXX_COMPILER_OK is not None:
        return _CXX_COMPILER_OK
    heal_cxx_include_path()
    _CXX_COMPILER_OK = False
    candidates: List[str] = []
    env_cxx = os.environ.get("CXX")
    if env_cxx and shutil.which(env_cxx) and shutil.which(env_cxx) not in candidates:
        candidates.append(env_cxx)
    for name in ("clang++", "g++", "c++"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    for cxx in candidates:
        try:
            completed = subprocess.run(
                [cxx, "-x", "c++", "-", "-fsyntax-only"],
                input=_PROBE_PROGRAM,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        _CXX_COMPILER_OK = completed.returncode == 0
        if _CXX_COMPILER_OK:
            break
    if not _CXX_COMPILER_OK:
        LOGGER.error(
            "no working C++ compiler probe: %s "
            "(Brian2 cython codegen would fail with CompileError at run time)",
            candidates or ["<none found>"],
        )
    return _CXX_COMPILER_OK


def cython_available() -> bool:
    """Whether the ``cython`` package is importable (Brian2's preferred target)."""
    return importlib.util.find_spec("cython") is not None


def resolve_codegen_target(target: str | None) -> str:
    """Map the configured codegen target onto one that can actually run here.

    Concrete targets pass through untouched (Brian2 still validates them).
    ``auto``/``None`` would otherwise trust Brian2 to pick ``cython`` first,
    which explodes with a ``CompileError`` mid-run when the C++ toolchain is
    broken; probe instead and fall back to numpy (slower, but never needs a
    compiler).
    """
    normalized = None if target is None else str(target).strip()
    if normalized and normalized != "auto":
        if normalized in {"cython", "cpp"} or normalized.endswith("codegen"):
            if not probe_cxx_compiler():
                LOGGER.warning(
                    "C++ toolchain probe failed; clang front-ends will raise "
                    "'ios' file not found on macOS CLT 26+. It is best to set "
                    "simulation.codegen_target to numpy in this environment"
                )
        return normalized
    if probe_cxx_compiler() and cython_available():
        return "cython"
    return "numpy"
