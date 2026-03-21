#!/usr/bin/env python3
"""Production-grade single-machine workflow artifact runtime helper.

This helper manages:
- incremental run id allocation via a locked registry
- atomic JSON artifact writes with per-artifact locks
- deterministic exact-path artifact reads
- strict runtime validation before downstream use

All writes stay inside: output/<currentdate>/<folder-name>/run-<id>/
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "forge.v2"
REGISTRY_FILENAME = "run-registry.json"
LOCKS_DIRNAME = ".locks"
RUN_STATUS_VALUES = {"active", "paused", "stopped", "complete", "failed"}
ARTIFACT_STATUS_VALUES = {"complete", "pending", "failed", "superseded", "reused"}


def sanitize_component(value: str, *, label: str, allow_dot: bool = False) -> str:
    pattern = r"[^a-z0-9._-]+" if allow_dot else r"[^a-z0-9_-]+"
    normalized = re.sub(pattern, "-", value.strip().lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-._")
    if not normalized:
        raise SystemExit(f"{label} must contain at least one letter or number")
    if normalized in {".", ".."}:
        raise SystemExit(f"{label} resolves to an unsafe path component")
    return normalized


def coerce_int(value: str, *, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise SystemExit(f"{label} must be an integer") from exc
    if parsed <= 0:
        raise SystemExit(f"{label} must be greater than zero")
    return parsed


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def current_date() -> str:
    return dt.date.today().isoformat()


def default_output_root() -> Path:
    return Path(__file__).resolve().parent / "output"


def resolve_output_root(raw: str | None) -> Path:
    root = Path(raw).expanduser().resolve() if raw else default_output_root().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_relative_to(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SystemExit(f"unsafe path outside output root: {path}") from exc


class FileLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        assert self.handle is not None
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()
        self.handle = None


def json_load(path: Path, *, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def atomic_write_json(path: Path, data: dict[str, Any], *, fail_if_exists: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fail_if_exists and path.exists():
        raise SystemExit(f"refusing to overwrite existing file: {path}")

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def registry_path(output_root: Path) -> Path:
    return output_root / REGISTRY_FILENAME


def registry_lock_path(output_root: Path) -> Path:
    return output_root / LOCKS_DIRNAME / f"{REGISTRY_FILENAME}.lock"


def load_registry(output_root: Path) -> dict[str, Any]:
    registry = json_load(registry_path(output_root), default=None)
    if registry is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "last_run_id": 0,
            "latest_run": None,
            "runs": []
        }
    if not isinstance(registry, dict):
        raise SystemExit("run registry must be a JSON object")
    return registry


def save_registry(output_root: Path, registry: dict[str, Any]) -> None:
    atomic_write_json(registry_path(output_root), registry)


def run_dir(output_root: Path, *, folder_name: str, date_value: str, run_id: int) -> Path:
    path = output_root / folder_name / date_value / f"run-{run_id:06d}"
    ensure_relative_to(path, output_root)
    return path


def artifact_filename(artifact_name: str, *, sequence: str | None = None) -> str:
    safe_artifact = sanitize_component(artifact_name, label="artifact-name")
    if sequence:
        safe_sequence = sanitize_component(sequence, label="sequence")
        return f"{safe_artifact}-{safe_sequence}.json"
    return f"{safe_artifact}.json"


def artifact_path(output_root: Path, *, folder_name: str, date_value: str, run_id: int, artifact_name: str, sequence: str | None = None) -> Path:
    path = run_dir(output_root, folder_name=folder_name, date_value=date_value, run_id=run_id) / artifact_filename(artifact_name, sequence=sequence)
    ensure_relative_to(path, output_root)
    return path


def artifact_lock_path(path: Path) -> Path:
    return path.parent / LOCKS_DIRNAME / f"{path.name}.lock"


def find_run_record(registry: dict[str, Any], run_id: int) -> dict[str, Any]:
    for record in registry.get("runs", []):
        if record.get("run_id") == run_id:
            return record
    raise SystemExit(f"run_id not found in registry: {run_id}")


def normalize_folder_name(folder_name: str) -> str:
    return sanitize_component(folder_name, label="folder-name")


def start_run(output_root: Path, folder_name: str) -> dict[str, Any]:
    safe_folder = normalize_folder_name(folder_name)
    with FileLock(registry_lock_path(output_root)):
        registry = load_registry(output_root)
        next_run_id = int(registry.get("last_run_id", 0)) + 1
        date_value = current_date()
        now = utc_now()
        run_record = {
            "run_id": next_run_id,
            "folder_name": safe_folder,
            "date": date_value,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "run_path": str(run_dir(output_root, folder_name=safe_folder, date_value=date_value, run_id=next_run_id)),
            "artifacts": {}
        }
        registry["last_run_id"] = next_run_id
        registry["latest_run"] = {
            "run_id": next_run_id,
            "folder_name": safe_folder,
            "date": date_value,
            "run_path": run_record["run_path"]
        }
        registry.setdefault("runs", []).append(run_record)
        save_registry(output_root, registry)
    run_dir(output_root, folder_name=safe_folder, date_value=date_value, run_id=next_run_id).mkdir(parents=True, exist_ok=True)
    return run_record


def update_run_status(output_root: Path, run_id: int, status: str) -> dict[str, Any]:
    if status not in RUN_STATUS_VALUES:
        raise SystemExit(f"invalid run status: {status}")
    with FileLock(registry_lock_path(output_root)):
        registry = load_registry(output_root)
        record = find_run_record(registry, run_id)
        record["status"] = status
        record["updated_at"] = utc_now()
        save_registry(output_root, registry)
    return record


def parse_json_content(args: argparse.Namespace) -> dict[str, Any]:
    raw = args.content if args.content is not None else sys.stdin.read()
    if not raw.strip():
        raise SystemExit("artifact payload JSON is required via --content or stdin")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"artifact payload must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("artifact payload JSON must be an object")
    return parsed


def build_artifact_document(
    *,
    namespace_path: str,
    run_record: dict[str, Any],
    artifact_name: str,
    producer: str,
    payload: dict[str, Any],
    sequence: str | None,
    input_artifacts: list[str],
    status: str,
) -> dict[str, Any]:
    if status not in ARTIFACT_STATUS_VALUES:
        raise SystemExit(f"invalid artifact status: {status}")
    return {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "run_id": run_record["run_id"],
            "folder_name": run_record["folder_name"],
            "date": run_record["date"],
            "namespace_path": namespace_path,
            "run_path": run_record["run_path"],
            "started_at": run_record["created_at"]
        },
        "artifact": {
            "name": sanitize_component(artifact_name, label="artifact-name"),
            "producer": producer,
            "created_at": utc_now(),
            "sequence": sanitize_component(sequence, label="sequence") if sequence else None,
            "input_artifacts": input_artifacts,
            "status": status
        },
        "payload": payload
    }


def validate_artifact_document(
    document: dict[str, Any],
    *,
    expected_run_id: int | None = None,
    expected_artifact_name: str | None = None,
    expected_producer: str | None = None,
    expected_namespace_path: str | None = None,
) -> None:
    if not isinstance(document, dict):
        raise SystemExit("artifact document must be a JSON object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit(f"artifact schema_version must equal {SCHEMA_VERSION}")

    run_section = document.get("run")
    artifact_section = document.get("artifact")
    payload = document.get("payload")
    if not isinstance(run_section, dict):
        raise SystemExit("artifact run section must be an object")
    if not isinstance(artifact_section, dict):
        raise SystemExit("artifact artifact section must be an object")
    if not isinstance(payload, dict) or not payload:
        raise SystemExit("artifact payload must be a non-empty JSON object")

    run_id = run_section.get("run_id")
    if not isinstance(run_id, int) or run_id <= 0:
        raise SystemExit("artifact run.run_id must be a positive integer")
    if expected_run_id is not None and run_id != expected_run_id:
        raise SystemExit(f"artifact run_id mismatch: expected {expected_run_id}, got {run_id}")

    if expected_namespace_path is not None and run_section.get("namespace_path") != expected_namespace_path:
        raise SystemExit("artifact namespace_path mismatch")

    artifact_name = artifact_section.get("name")
    if not isinstance(artifact_name, str) or not artifact_name:
        raise SystemExit("artifact name must be a non-empty string")
    if expected_artifact_name is not None and artifact_name != sanitize_component(expected_artifact_name, label="artifact-name"):
        raise SystemExit(f"artifact name mismatch: expected {expected_artifact_name}, got {artifact_name}")

    producer = artifact_section.get("producer")
    if not isinstance(producer, str) or not producer:
        raise SystemExit("artifact producer must be a non-empty string")
    if expected_producer is not None and producer != expected_producer:
        raise SystemExit(f"artifact producer mismatch: expected {expected_producer}, got {producer}")

    status = artifact_section.get("status")
    if status not in ARTIFACT_STATUS_VALUES:
        raise SystemExit("artifact status is invalid")
    if status != "complete":
        raise SystemExit(f"artifact status must be complete for downstream use, got {status}")

    input_artifacts = artifact_section.get("input_artifacts")
    if not isinstance(input_artifacts, list):
        raise SystemExit("artifact input_artifacts must be a list")


def write_artifact(args: argparse.Namespace) -> dict[str, Any]:
    output_root = resolve_output_root(args.output_root)
    run_id = coerce_int(args.run_id, label="run-id")
    safe_artifact = sanitize_component(args.artifact_name, label="artifact-name")
    sequence = sanitize_component(args.sequence, label="sequence") if args.sequence else None
    namespace_path = args.namespace_path.strip()
    if not namespace_path:
        raise SystemExit("namespace-path is required")
    payload = parse_json_content(args)
    input_artifacts = args.input_artifact or []
    status = args.status or "complete"

    with FileLock(registry_lock_path(output_root)):
        registry = load_registry(output_root)
        run_record = find_run_record(registry, run_id)
        target_path = artifact_path(
            output_root,
            folder_name=run_record["folder_name"],
            date_value=run_record["date"],
            run_id=run_id,
            artifact_name=safe_artifact,
            sequence=sequence,
        )
        lock_path = artifact_lock_path(target_path)
        document = build_artifact_document(
            namespace_path=namespace_path,
            run_record=run_record,
            artifact_name=safe_artifact,
            producer=args.producer,
            payload=payload,
            sequence=sequence,
            input_artifacts=input_artifacts,
            status=status,
        )
        with FileLock(lock_path):
            atomic_write_json(target_path, document, fail_if_exists=args.fail_if_exists)
        run_record.setdefault("artifacts", {})[safe_artifact if not sequence else f"{safe_artifact}:{sequence}"] = {
            "path": str(target_path),
            "updated_at": utc_now(),
            "producer": args.producer,
            "status": status
        }
        run_record["updated_at"] = utc_now()
        registry["latest_run"] = {
            "run_id": run_record["run_id"],
            "folder_name": run_record["folder_name"],
            "date": run_record["date"],
            "run_path": run_record["run_path"]
        }
        save_registry(output_root, registry)
    return {
        "run_id": run_id,
        "artifact_name": safe_artifact,
        "path": str(target_path)
    }


def validate_artifact(args: argparse.Namespace) -> dict[str, Any]:
    output_root = resolve_output_root(args.output_root)
    path = Path(args.path).expanduser().resolve()
    ensure_relative_to(path, output_root)
    document = json_load(path)
    validate_artifact_document(
        document,
        expected_run_id=coerce_int(args.run_id, label="run-id") if args.run_id else None,
        expected_artifact_name=args.artifact_name,
        expected_producer=args.producer,
        expected_namespace_path=args.namespace_path,
    )
    return {
        "valid": True,
        "path": str(path),
        "run_id": document["run"]["run_id"],
        "artifact_name": document["artifact"]["name"],
        "producer": document["artifact"]["producer"]
    }


def read_artifact(args: argparse.Namespace) -> dict[str, Any]:
    output_root = resolve_output_root(args.output_root)
    path = Path(args.path).expanduser().resolve()
    ensure_relative_to(path, output_root)
    document = json_load(path)
    validate_artifact_document(
        document,
        expected_run_id=coerce_int(args.run_id, label="run-id") if args.run_id else None,
        expected_artifact_name=args.artifact_name,
        expected_producer=args.producer,
        expected_namespace_path=args.namespace_path,
    )
    return document


def latest_run(output_root: Path) -> dict[str, Any]:
    registry = load_registry(output_root)
    latest = registry.get("latest_run")
    if not latest:
        raise SystemExit("no runs have been started yet")
    return latest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start-run")
    start.add_argument("--folder-name", required=True)

    status = subparsers.add_parser("set-run-status")
    status.add_argument("--run-id", required=True)
    status.add_argument("--status", required=True)

    latest = subparsers.add_parser("latest-run")

    write = subparsers.add_parser("write")
    write.add_argument("--run-id", required=True)
    write.add_argument("--namespace-path", required=True)
    write.add_argument("--artifact-name", required=True)
    write.add_argument("--producer", required=True)
    write.add_argument("--content")
    write.add_argument("--sequence")
    write.add_argument("--status", default="complete")
    write.add_argument("--input-artifact", action="append")
    write.add_argument("--fail-if-exists", action="store_true")

    validate = subparsers.add_parser("validate")
    validate.add_argument("--path", required=True)
    validate.add_argument("--run-id")
    validate.add_argument("--namespace-path")
    validate.add_argument("--artifact-name")
    validate.add_argument("--producer")

    read = subparsers.add_parser("read")
    read.add_argument("--path", required=True)
    read.add_argument("--run-id")
    read.add_argument("--namespace-path")
    read.add_argument("--artifact-name")
    read.add_argument("--producer")

    return parser


def emit(result: dict[str, Any]) -> int:
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    output_root = resolve_output_root(args.output_root)

    if args.command == "start-run":
        return emit(start_run(output_root, args.folder_name))
    if args.command == "set-run-status":
        return emit(update_run_status(output_root, coerce_int(args.run_id, label="run-id"), args.status))
    if args.command == "latest-run":
        return emit(latest_run(output_root))
    if args.command == "write":
        return emit(write_artifact(args))
    if args.command == "validate":
        return emit(validate_artifact(args))
    if args.command == "read":
        return emit(read_artifact(args))
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
