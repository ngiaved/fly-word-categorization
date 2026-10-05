"""Configuration loading, validation, and dotted-path overrides.

Every tunable parameter lives in a YAML file. Code reads parameters through
``Config`` and never hard-codes a value that changes numerical behaviour.
Keys absent from the defaults are rejected so typos fail loudly.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Iterable

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "default.yaml"


class ConfigError(ValueError):
    """Raised for malformed, unknown, or mistyped configuration."""


class _Missing:
    def __repr__(self) -> str:
        return "<missing>"


_MISSING = _Missing()


def _coerce(text: str) -> Any:
    """Parse a CLI override string into a YAML scalar."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into a copy of ``base``."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _type_name(value: Any) -> str:
    return type(value).__name__


def _check_types(defaults: dict, actual: dict, prefix: str = "") -> list[str]:
    """Return human-readable errors where ``actual`` departs from defaults."""
    errors: list[str] = []
    for key, default_value in defaults.items():
        path = f"{prefix}{key}"
        if key not in actual:
            errors.append(f"missing required key: {path}")
            continue
        value = actual[key]
        if isinstance(default_value, dict):
            if not isinstance(value, dict):
                errors.append(
                    f"{path}: expected a mapping, got {_type_name(value)}"
                )
                continue
            errors.extend(_check_types(default_value, value, prefix=f"{path}."))
        elif isinstance(default_value, bool):
            if not isinstance(value, bool):
                errors.append(f"{path}: expected true/false, got {value!r}")
        elif isinstance(default_value, int) and isinstance(value, bool):
            errors.append(f"{path}: expected {_type_name(default_value)}, got bool")
        elif isinstance(default_value, (int, float)) and not isinstance(
            value, (int, float)
        ):
            errors.append(
                f"{path}: expected {_type_name(default_value)}, got {_type_name(value)}"
            )
        elif isinstance(default_value, str) and not isinstance(value, str):
            errors.append(f"{path}: expected a string, got {_type_name(value)}")
        elif default_value is None and value is not None:
            errors.append(f"{path}: expected null, got {value!r}")
    return errors


def _reject_unknown(defaults: dict, actual: dict, prefix: str = "") -> list[str]:
    errors: list[str] = []
    for key in actual:
        path = f"{prefix}{key}"
        if key not in defaults:
            errors.append(
                f"unknown configuration key: {path} "
                f"(expected one of: {', '.join(sorted(defaults))})"
            )
        elif isinstance(defaults[key], dict) and isinstance(actual[key], dict):
            errors.extend(_reject_unknown(defaults[key], actual[key], f"{path}."))
    return errors


class Config:
    """Nested configuration with dotted-path access."""

    def __init__(self, data: dict, source: str = "<memory>") -> None:
        self._data = data
        self.source = source

    @classmethod
    def load(
        cls,
        path: str | os.PathLike[str] | None = None,
        overrides: Iterable[str] = (),
        use_defaults: bool = True,
    ) -> "Config":
        """Load defaults, merge a YAML file, then apply ``key=value`` overrides.

        ``overrides`` entries look like ``network.dt=0.5ms``.
        """
        resolved = Path(path) if path else DEFAULT_CONFIG_PATH
        if not resolved.exists():
            if path is not None:
                raise ConfigError(f"configuration file not found: {resolved}")
            raise ConfigError(
                f"default configuration not found at {DEFAULT_CONFIG_PATH}; "
                "run from the repository or pass --config"
            )
        with open(resolved, "r", encoding="utf-8") as handle:
            user_data = yaml.safe_load(handle) or {}
        if not isinstance(user_data, dict):
            raise ConfigError(f"{resolved}: top level must be a mapping")

        base = _read_default_config()
        if not use_defaults:
            base = user_data
        elif user_data:
            errors = _reject_unknown(base, user_data)
            if errors:
                raise ConfigError(
                    f"{resolved}: invalid configuration\n  " + "\n  ".join(errors)
                )
        data = deep_merge(base, user_data) if use_defaults else user_data

        config = cls(data, source=str(resolved))
        config.apply_overrides(overrides)
        return config

    def apply_overrides(self, overrides: Iterable[str]) -> None:
        parsed: dict = {}
        for item in overrides:
            if "=" not in item:
                raise ConfigError(
                    f"override {item!r} must look like section.key=value"
                )
            key, raw_value = item.split("=", 1)
            key = key.strip()
            if not key:
                raise ConfigError(f"override {item!r} has an empty key")
            parsed = deep_merge(parsed, _expand(key, _coerce(raw_value.strip())))
        if not parsed:
            return
        errors = _reject_unknown(self._data, parsed)
        if errors:
            raise ConfigError("invalid override\n  " + "\n  ".join(errors))
        self._data = deep_merge(self._data, parsed)

    def with_overrides(self, overrides: dict[str, Any]) -> "Config":
        """Return an independent copy with ``dotted.path`` keys replaced.

        Unlike :meth:`apply_overrides` this never mutates ``self``, which the
        calibration search relies on: it derives a candidate configuration per
        grid point and must be able to discard it without disturbing the base.
        Values are used as-is (no string coercion), so callers can pass floats.
        """
        if not overrides:
            return Config(self.to_dict(), self.source)
        parsed: dict = {}
        for key, value in overrides.items():
            parsed = deep_merge(parsed, _expand(key.strip(), value))
        errors = _reject_unknown(self._data, parsed)
        if errors:
            raise ConfigError("invalid override\n  " + "\n  ".join(errors))
        return Config(deep_merge(self.to_dict(), parsed), self.source)

    def get(self, dotted: str, default: Any = _MISSING) -> Any:  # type: ignore[name-defined]
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                if default is _MISSING:
                    raise ConfigError(f"no such configuration key: {dotted}")
                return default
            node = node[part]
        return node

    def section(self, dotted: str) -> dict:
        value = self.get(dotted)
        if not isinstance(value, dict):
            raise ConfigError(f"configuration key {dotted} is not a section")
        return copy.deepcopy(value)

    def sequence(self, dotted: str) -> list:
        value = self.get(dotted)
        if not isinstance(value, list):
            raise ConfigError(
                f"configuration key {dotted} is not a list (got {_type_name(value)})"
            )
        return copy.deepcopy(value)

    def validate(self) -> None:
        """Re-check types against the default schema."""
        base = _read_default_config()
        errors = _check_types(base, self._data)
        if errors:
            raise ConfigError(
                "configuration validation failed\n  " + "\n  ".join(errors)
            )
        self._validate_ranges()

    def _validate_ranges(self) -> None:
        checks: list[tuple[str, float, float]] = [
            ("simulation.dt", 0.0, 1e9),
            ("simulation.rest_ms", 0.0, 1e9),
            ("encoding.grid_rows", 1.0, 4096.0),
            ("encoding.grid_cols", 1.0, 4096.0),
            ("encoding.letter_spacing", 0.0, 1e6),
            ("encoding.baseline_hz", 0.0, 1e9),
            ("encoding.max_hz", 0.0, 1e9),
            ("learning.trace_tau_ms", 0.0, 1e9),
            ("learning.learning_rate", 0.0, 1e9),
            ("learning.w_min", 0.0, 1e9),
            ("learning.w_max", 0.0, 1e9),
            ("evaluation.chance_level", 0.0, 1.0),
        ]
        for dotted, low, high in checks:
            try:
                value = float(self.get(dotted))
            except (ConfigError, TypeError, ValueError):
                continue
            if not low <= value <= high:
                raise ConfigError(
                    f"{dotted} = {value} is outside the allowed range [{low}, {high}]"
                )
        if float(self.get("learning.w_min")) >= float(self.get("learning.w_max")):
            raise ConfigError("learning.w_min must be less than learning.w_max")
        baseline, peak = (
            float(self.get("encoding.baseline_hz")),
            float(self.get("encoding.max_hz")),
        )
        if baseline > peak:
            raise ConfigError("encoding.baseline_hz must not exceed encoding.max_hz")
        n_categories = len(self.get("encoding.categories"))
        if not n_categories:
            raise ConfigError("encoding.categories must not be empty")

    def to_dict(self) -> dict:
        return copy.deepcopy(self._data)

    def __repr__(self) -> str:
        return f"Config(source={self.source!r}, keys={sorted(self._data)})"


def _expand(dotted: str, value: Any) -> dict:
    parts = dotted.split(".")
    node: Any = value
    for part in reversed(parts[1:]):
        node = {part: node}
    return {parts[0]: node}


_DEFAULT_CACHE: dict | None = None


def _read_default_config() -> dict:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        with open(DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as handle:
            _DEFAULT_CACHE = yaml.safe_load(handle) or {}
    return copy.deepcopy(_DEFAULT_CACHE)