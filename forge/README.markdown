# Forge Pipeline

`forge` is a staged agent-generation pipeline that converts bounded research inputs into OpenCode agent specs and a machine-readable orchestration manifest.

## What Changed

Forge now separates orchestration from JSON checking.

- `forge/main.md` orchestrates only.
- Producer subagents write their JSON artifacts to `forge/output/...` in real time.
- The same producer subagent then calls one shared checker agent on the exact saved JSON path.
- The checker reads the file from disk, writes its own checker result artifact, and returns metadata only.
- After each checker result, the producer calls `python forge/save_workflow_artifact.py record-check ... --max-attempts 5`.
- The runtime, not the prompt alone, counts checker attempts.
- If checker approval still fails after 5 attempts, the runtime writes `workflow-error.json`, marks the run failed, and the workflow stops.
- Final non-JSON files are expected to be reviewed later by a different final-file validator.
- Forge does not support best-effort generation. If required information is missing or ambiguous, it must stop and ask for the missing input.

## Main Roles

- `forge/main.md`: orchestrates the run, input scope, run allocation, helper sequencing, manifest synthesis, support-script synthesis, and final file writes.
- `forge/agent-forge-reader.md`: reads approved research and writes `reader-summary.json`.
- `forge/agent-forge-analyst.md`: converts the summary into `workflow-plan.json`.
- `forge/agent-forge-collision-resolver.md`: resolves naming conflicts in `collision-resolution.json`.
- `forge/agent-forge-designer.md`: writes the generated design package in `generated-agents.json`.
- `forge/agent-forge-critic.md`: reviews the design package and writes `critic-review.json`.
- `forge/agent-forge-checker.md`: checks saved Forge JSON artifacts on disk and writes `*-check.json` results.

## JSON Check Flow

For every producer subagent output:

1. Write the JSON artifact to `forge/output/...`.
2. Validate the saved artifact with `forge/save_workflow_artifact.py`.
3. Call `forge/agent-forge-checker` on the exact saved path.
4. Call `python forge/save_workflow_artifact.py record-check ... --max-attempts 5` with the checked path and checker result path.
5. If `record-check` returns `approved`, continue.
6. If `record-check` returns `retry`, repair and repeat.
7. If `record-check` returns `failed`, the runtime has already written `workflow-error.json` and stopped the run.

`forge/main.md` does not own semantic validation anymore. It only routes based on saved checker outcomes and saved error artifacts.

## No Guessing

- Forge must not guess missing workflow requirements.
- Forge must not silently continue on low-confidence or incomplete source material.
- If required information is missing or ambiguous, Forge stops and asks for the exact missing input.
- This applies to source files, approvals, role ownership, artifacts, checkpoints, and other workflow-shaping details.

## Saved Artifact Layout

Forge uses disk-backed JSON artifacts:

- `output/<currentdate>/<folder-name>/run-<zero-padded-run-id>/reader-summary.json`
- `output/<currentdate>/<folder-name>/run-<zero-padded-run-id>/workflow-plan.json`
- `output/<currentdate>/<folder-name>/run-<zero-padded-run-id>/collision-resolution.json`
- `output/<currentdate>/<folder-name>/run-<zero-padded-run-id>/generated-agents.json`
- `output/<currentdate>/<folder-name>/run-<zero-padded-run-id>/critic-review.json`
- `output/<currentdate>/<folder-name>/run-<zero-padded-run-id>/<artifact-type>-check.json`
- `output/<currentdate>/<folder-name>/run-<zero-padded-run-id>/workflow-error.json`

## Checker Scope

`forge/agent-forge-checker.md` checks only saved JSON artifacts.

Supported artifact types:

- `reader-summary`
- `workflow-plan`
- `collision-resolution`
- `generated-agents`
- `critic-review`

The checker does not validate final `.md` files or the final Python helper script.

## Failure Model

- Each producer subagent gets at most 5 checker invocations for one output.
- If approval still fails after 5 tries, `record-check` writes `workflow-error.json` automatically.
- `record-check` also marks the run failed automatically.
- `forge/main.md` stops when it receives `artifact_name: workflow-error`.
- Forge fails closed rather than silently continuing with an unapproved JSON artifact.

## Pattern Support

Forge still supports these `forge.v2` workflow types:

- `single-agent`
- `sequential`
- `parallel`
- `loop`
- `review-and-critique`
- `iterative-refinement`
- `coordinator`
- `hierarchical-task-decomposition`
- `swarm`
- `human-in-the-loop`
- `custom-logic`

`ReAct` remains a role behavior overlay, not the default top-level workflow type.

## Runtime Helper

`forge/save_workflow_artifact.py` remains the canonical runtime helper for:

- `start-run`
- `set-run-status`
- `write`
- `validate`
- `read`
- `record-check`

Forge uses it for deterministic run ids, path-safe JSON envelopes, exact-path reads, atomic artifact writes, checker-attempt tracking, and automatic `workflow-error.json` creation on the 5th failed check.
