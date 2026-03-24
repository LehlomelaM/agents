#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


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


def render_prompt(case: dict, workspace_root: str) -> str:
    prompt_path = WORKSPACE_ROOT / case["prompt_template_path"]
    text = prompt_path.read_text()
    return text.replace("{{WORKSPACE_ROOT}}", workspace_root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render one forge eval prompt")
    parser.add_argument("--case", required=True, help="Case id from forge/forge-evals/suite.json")
    parser.add_argument(
        "--workspace-root",
        default=str(WORKSPACE_ROOT),
        help="Workspace root path used to replace {{WORKSPACE_ROOT}}",
    )
    parser.add_argument("--show-agent", action="store_true", help="Print target agent before the prompt")
    args = parser.parse_args()

    suite = load_suite()
    case = get_case(suite, args.case)
    prompt = render_prompt(case, args.workspace_root)

    if args.show_agent:
        sys.stdout.write(f"TARGET_AGENT={case['target_agent']}\n")
    sys.stdout.write(prompt)
    if not prompt.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
