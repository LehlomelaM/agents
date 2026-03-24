# Forge Evals

This directory contains lightweight, fixture-based evals for the agents in `./forge`.

These evals are intentionally simple:

- each case targets one forge agent
- each case uses explicit inputs only
- each case has a prompt template and expected assertions
- prompts use `{{WORKSPACE_ROOT}}`, which the helper script expands automatically

## Files

- `suite.json` - suite manifest and assertion metadata
- `prompts/` - prompt templates for each eval case
- `fixtures/` - source files used by eval prompts
- `render_prompt.py` - renders one prompt with `{{WORKSPACE_ROOT}}` expanded
- `verify_output.py` - performs lightweight checks against saved output and, when present, the saved artifact payload

## Cases

- `reader-basic`
- `analyst-basic`
- `designer-basic`
- `critic-basic`
- `collision-resolver-basic`
- `main-basic`

## Manual flow

1. Render a prompt:

```bash
python3 forge/forge-evals/render_prompt.py --case reader-basic
```

2. Send that rendered prompt to the target agent named in `suite.json`.

3. Save the agent metadata output to a file.

4. Verify the metadata output and its saved artifact:

```bash
python3 forge/forge-evals/verify_output.py --case reader-basic --output /tmp/reader-output.txt
```

## Notes

- These are lightweight evals, not a full grading harness.
- Assertions focus on contract fidelity, required keys, important substrings, and saved artifact payload checks.
- The `main-basic` case is an integration-style eval for `forge/main.md`, so it is less strict than the pure JSON helper cases.
