#!/usr/bin/env python3
"""Validate a release capability configuration and expose GitHub Action outputs."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Never


VALID_CHECKS = frozenset(
    {
        "ruff",
        "ruff-format",
        "mypy",
        "pytest",
        "pre-commit",
        "docker-compose",
        "sqlfluff",
        "dbt-deps",
        "dbt-parse",
    }
)
VALID_NODE_CHECKS = frozenset({"lint", "typecheck", "test", "build"})
TOP_LEVEL_TABLES = frozenset({"version", "project", "python", "validation", "build", "node", "dbt", "release"})
TABLE_FIELDS = {
    "project": frozenset({"working_directory"}),
    "python": frozenset({"version", "dependency_group"}),
    "validation": frozenset({"checks"}),
    "build": frozenset({"python_package"}),
    "node": frozenset({"directory", "version", "checks"}),
    "dbt": frozenset({"profiles_example"}),
    "release": frozenset({"semantic_release"}),
}


def fail(message: str) -> Never:
    """Report a configuration error without a traceback."""
    raise SystemExit(f"release configuration error: {message}")


def mapping(value: object, name: str) -> dict[str, object]:
    """Return a TOML table with a useful diagnostic when it is absent or invalid."""
    if not isinstance(value, dict):
        fail(f"[{name}] must be a TOML table.")
    return value


def string(value: object, name: str, default: str | None = None) -> str:
    """Return a non-empty string field."""
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value:
        fail(f"{name} must be a non-empty string.")
    return value


def boolean(value: object, name: str, default: bool = False) -> bool:
    """Return a boolean field."""
    if value is None:
        return default
    if not isinstance(value, bool):
        fail(f"{name} must be true or false.")
    return value


def strings(value: object, name: str, allowed: frozenset[str]) -> set[str]:
    """Validate a list of distinct capability names."""
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        fail(f"{name} must be a non-empty list of capability names.")
    values = set(value)
    if len(values) != len(value):
        fail(f"{name} must not contain duplicate capabilities.")
    unknown = values - allowed
    if unknown:
        fail(f"{name} contains unsupported capability names: {', '.join(sorted(unknown))}.")
    return values


def relative_directory(value: str, name: str) -> str:
    """Reject absolute and parent-relative paths supplied by consumer configuration."""
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not re.fullmatch(r"[A-Za-z0-9._/-]+", value):
        fail(f"{name} must be a safe repository-relative path without '..'.")
    return value


def identifier(value: str, name: str) -> str:
    """Validate a command argument sourced from repository configuration."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        fail(f"{name} may contain only letters, numbers, '.', '_', and '-'.")
    return value


def validate_table_fields(table: dict[str, object], table_name: str) -> None:
    """Reject misspelled or unsupported schema fields instead of ignoring them."""
    unexpected = set(table) - TABLE_FIELDS[table_name]
    if unexpected:
        fail(f"[{table_name}] contains unsupported fields: {', '.join(sorted(unexpected))}.")


def emit(name: str, value: str | bool) -> None:
    """Write a single GitHub Actions output."""
    output = os.environ.get("GITHUB_OUTPUT")
    if output is None:
        return
    normalized = str(value).lower() if isinstance(value, bool) else value
    with Path(output).open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={normalized}\n")


def load_config(config_path: Path) -> dict[str, object]:
    """Load a TOML document and report syntax errors consistently."""
    try:
        with config_path.open("rb") as stream:
            data = tomllib.load(stream)
    except FileNotFoundError:
        fail(f"required file {config_path} was not found.")
    except tomllib.TOMLDecodeError as error:
        fail(f"{config_path} is not valid TOML: {error}.")
    if not isinstance(data, dict):
        fail("configuration root must be a TOML table.")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to .github/release.toml")
    args = parser.parse_args()
    config = load_config(args.config)

    unknown_tables = set(config) - TOP_LEVEL_TABLES
    if unknown_tables:
        fail(f"configuration contains unsupported tables or fields: {', '.join(sorted(unknown_tables))}.")
    if config.get("version") != 1:
        fail("version must be the supported schema version: 1.")
    project = mapping(config.get("project", {}), "project")
    python = mapping(config.get("python"), "python")
    validation = mapping(config.get("validation"), "validation")
    release = mapping(config.get("release"), "release")
    for table_name, table in {
        "project": project,
        "python": python,
        "validation": validation,
        "build": mapping(config.get("build", {}), "build"),
        "node": mapping(config.get("node", {}), "node"),
        "dbt": mapping(config.get("dbt", {}), "dbt"),
        "release": release,
    }.items():
        validate_table_fields(table, table_name)

    working_directory = relative_directory(
        string(project.get("working_directory"), "project.working_directory", "."),
        "project.working_directory",
    )
    python_version = string(python.get("version"), "python.version", "3.14")
    dependency_group = identifier(
        string(python.get("dependency_group"), "python.dependency_group", "dev"),
        "python.dependency_group",
    )
    checks = strings(validation.get("checks"), "validation.checks", VALID_CHECKS)
    if not boolean(release.get("semantic_release"), "release.semantic_release"):
        fail("release.semantic_release must be true for the canonical release contract.")

    root = args.config.parent.parent
    python_root = root / working_directory
    if not (python_root / "pyproject.toml").is_file() or not (python_root / "uv.lock").is_file():
        fail(f"project.working_directory '{working_directory}' must contain pyproject.toml and uv.lock.")

    build = mapping(config.get("build", {}), "build")
    python_package = boolean(build.get("python_package"), "build.python_package")

    node = mapping(config.get("node", {}), "node")
    node_enabled = bool(node)
    node_directory = "."
    node_version = "24"
    node_checks: set[str] = set()
    if node_enabled:
        node_directory = relative_directory(string(node.get("directory"), "node.directory"), "node.directory")
        node_version = string(node.get("version"), "node.version", "24")
        node_checks = strings(node.get("checks"), "node.checks", VALID_NODE_CHECKS)
        if not (root / node_directory / "package-lock.json").is_file():
            fail(f"node.directory '{node_directory}' must contain package-lock.json.")

    dbt = mapping(config.get("dbt", {}), "dbt")
    profiles_example = relative_directory(
        string(dbt.get("profiles_example"), "dbt.profiles_example", "profiles.example.yml"),
        "dbt.profiles_example",
    )
    if "dbt-parse" in checks:
        if "dbt-deps" not in checks:
            fail("validation.checks must include dbt-deps when it includes dbt-parse.")
        if not (python_root / profiles_example).is_file():
            fail(f"dbt.profiles_example '{profiles_example}' was not found in project.working_directory.")

    emit("working_directory", working_directory)
    emit("python_version", python_version)
    emit("dependency_group", dependency_group)
    emit("build_python_package", python_package)
    emit("node_enabled", node_enabled)
    emit("node_directory", node_directory)
    emit("node_version", node_version)
    emit("dbt_profiles_example", profiles_example)
    for check in VALID_CHECKS:
        emit(f"check_{check.replace('-', '_')}", check in checks)
    for check in VALID_NODE_CHECKS:
        emit(f"node_check_{check}", check in node_checks)


if __name__ == "__main__":
    main()
