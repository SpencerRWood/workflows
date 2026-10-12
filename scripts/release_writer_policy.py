"""Check supplied branch ruleset details; never apply policy or attest registry ACLs."""

import argparse
import json
import sys
from pathlib import Path


def validate(rulesets: list[dict], app_id: int) -> None:
    if isinstance(app_id, bool) or app_id <= 0:
        raise ValueError("a verified positive release App ID is required")
    main = [
        rule
        for rule in rulesets
        if rule.get("enforcement") == "active"
        and rule.get("target") == "branch"
        and rule.get("conditions", {}).get("ref_name")
        == {"include": ["refs/heads/main"], "exclude": []}
    ]
    bypass = [
        {"actor_id": app_id, "actor_type": "Integration", "bypass_mode": "always"}
    ]
    if not any(
        {r["type"] for r in rule.get("rules", [])} == {"update"}
        and rule.get("bypass_actors", []) == bypass
        for rule in main
    ):
        raise ValueError(
            "main needs an active update-only rule with the sole reviewed App bypass"
        )
    if not any(
        {r["type"] for r in rule.get("rules", [])} >= {"deletion", "non_fast_forward"}
        and rule.get("bypass_actors", []) == []
        for rule in rulesets
        if rule.get("enforcement") == "active"
        and rule.get("target") == "branch"
        and rule.get("conditions", {}).get("ref_name")
        in (
            {"include": ["refs/heads/main"], "exclude": []},
            {"include": ["~ALL"], "exclude": []},
        )
    ):
        raise ValueError(
            "main needs independent deletion/force-push rules with no bypass"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rulesets", type=Path, required=True)
    parser.add_argument("--app-id", type=int, required=True)
    args = parser.parse_args()
    try:
        validate(json.loads(args.rulesets.read_text()), args.app_id)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Do not echo input files or exception payloads into diagnostics.
        print(
            json.dumps(
                {"status": "blocked", "reason": "invalid_or_insufficient_main_rulesets"}
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "consistent",
                "scope": "supplied_main_rulesets_only",
                "live_enforcement": "unverified",
                "ghcr_exclusivity": "unverified",
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
