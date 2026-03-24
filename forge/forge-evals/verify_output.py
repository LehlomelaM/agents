#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


FORGE_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = FORGE_ROOT.parent
SUITE_PATH = FORGE_ROOT / "forge-evals" / "suite.json"


def load_suite() -> dict:
    return json.loads(SUITE_PATH.read_text())


def get_case(suite: dict, case_id: str) -> dict:
    for case in suite["cases"]:
        if case["id"] == case_id:
            return case
    raise SystemExit(f"unknown case: {case_id}")


def verify_json_keys(data: dict, keys: list[str]) -> list[str]:
    errors = []
    for key in keys:
        if key not in data:
            errors.append(f"missing required json key: {key}")
    return errors


def verify_substrings(haystacks: list[str], needles: list[str], *, kind: str) -> list[str]:
    errors = []
    for needle in needles:
        if not any(needle in hay for hay in haystacks):
            errors.append(f"missing required {kind} substring: {needle}")
    return errors


def verify_forbidden_substrings(haystacks: list[str], needles: list[str], *, kind: str) -> list[str]:
    errors = []
    for needle in needles:
        if any(needle in hay for hay in haystacks):
            errors.append(f"found forbidden {kind} substring: {needle}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one saved forge eval output")
    parser.add_argument("--case", required=True, help="Case id from forge/forge-evals/suite.json")
    parser.add_argument("--output", required=True, help="Path to saved agent output")
    args = parser.parse_args()

    suite = load_suite()
    case = get_case(suite, args.case)
    assertions = case["assertions"]
    text = Path(args.output).read_text()
    errors: list[str] = []

    artifact_haystacks: list[str] = []
    if assertions["output_type"] == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"output is not valid json: {exc}")
        errors.extend(verify_json_keys(data, assertions.get("required_json_keys", [])))
        compact_json = json.dumps(data, separators=(",", ":"), sort_keys=True)
        pretty_json = json.dumps(data, indent=2, sort_keys=True)
        if "artifact_path" in data:
            artifact_path = Path(data["artifact_path"])
            if not artifact_path.exists():
                errors.append(f"artifact path does not exist: {artifact_path}")
            else:
                artifact_text = artifact_path.read_text()
                try:
                    artifact_data = json.loads(artifact_text)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"artifact is not valid json: {exc}")
                errors.extend(verify_json_keys(artifact_data, assertions.get("required_artifact_json_keys", [])))
                artifact_haystacks = [
                    artifact_text,
                    json.dumps(artifact_data, separators=(",", ":"), sort_keys=True),
                    json.dumps(artifact_data, indent=2, sort_keys=True),
                ]
    else:
        compact_json = text
        pretty_json = text

    output_haystacks = [text, compact_json, pretty_json]
    errors.extend(verify_substrings(output_haystacks, assertions.get("required_substrings", []), kind="output"))
    errors.extend(verify_forbidden_substrings(output_haystacks, assertions.get("forbidden_substrings", []), kind="output"))
    if artifact_haystacks:
        errors.extend(verify_substrings(artifact_haystacks, assertions.get("required_payload_substrings", []), kind="artifact"))
        errors.extend(verify_forbidden_substrings(artifact_haystacks, assertions.get("forbidden_payload_substrings", []), kind="artifact"))

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        raise SystemExit(1)

    print(f"PASS: {args.case}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
