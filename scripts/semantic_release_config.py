#!/usr/bin/env python3
"""Build a temporary semantic-release config from a consumer's pyproject.toml."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pyproject", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    with args.pyproject.open("rb") as stream:
        config = tomllib.load(stream)["tool"]["semantic_release"]
    config["commit_parser"] = f"{Path(__file__).with_name('dependency_release_parser.py').resolve()}:DependencyReleaseParser"
    args.output.write_text(json.dumps({"semantic_release": config}), encoding="utf-8")


if __name__ == "__main__":
    main()
