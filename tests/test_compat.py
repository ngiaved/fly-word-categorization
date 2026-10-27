"""Tests for the toolchain compatibility helpers in ``flyread.compat``."""

from flyread import compat


def test_concrete_codegen_targets_pass_through(monkeypatch):
    for value in ("numpy", "cython", "cpp"):
        assert compat.resolve_codegen_target(value) == value


def test_auto_resolves_to_cython_when_toolchain_works(monkeypatch):
    monkeypatch.setattr(compat, "probe_cxx_compiler", lambda timeout=30.0: True)
    monkeypatch.setattr(compat, "cython_available", lambda: True)
    assert compat.resolve_codegen_target("auto") == "cython"
    assert compat.resolve_codegen_target(None) == "cython"


def test_auto_falls_back_to_numpy_without_working_compiler(monkeypatch):
    monkeypatch.setattr(compat, "probe_cxx_compiler", lambda timeout=30.0: False)
    monkeypatch.setattr(compat, "cython_available", lambda: True)
    assert compat.resolve_codegen_target("auto") == "numpy"
    assert compat.resolve_codegen_target(None) == "numpy"


def test_auto_falls_back_to_numpy_without_cython(monkeypatch):
    monkeypatch.setattr(compat, "probe_cxx_compiler", lambda timeout=30.0: True)
    monkeypatch.setattr(compat, "cython_available", lambda: False)
    assert compat.resolve_codegen_target("auto") == "numpy"


def test_probe_runs_compiler_at_most_once(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(compat.subprocess, "run", fake_run)
    monkeypatch.setattr(compat, "_CXX_COMPILER_OK", None)
    monkeypatch.setattr(compat, "heal_cxx_include_path", lambda: False)
    monkeypatch.setattr(compat.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert compat.probe_cxx_compiler() is True
    assert compat.probe_cxx_compiler() is True
    assert len(calls) == 1
    assert calls[0][0] == "/usr/bin/clang++"

    compat._CXX_COMPILER_OK = None
