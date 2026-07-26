"""CLI for repository context discovery and deterministic quality gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

CONFIG_PATH = Path("ai/harness.json")
BACKLOG_STATUSES = {"idea", "ready", "in_progress", "blocked", "done"}
PRD_SECTIONS = (
    "Outcome",
    "Problem and evidence",
    "Scope",
    "Acceptance criteria",
    "Ralph slices",
    "Verification",
)
PRD_STATUS_LABELS = {
    "idea": "Idea",
    "ready": "Ready",
    "in_progress": "In Progress",
    "blocked": "Blocked",
    "done": "Done",
}
TRANSACTION_PATH = Path("ai/.ralph-transaction.json")
RECEIPT_DIRECTORY = Path("ai/ralph/receipts")


class HarnessError(RuntimeError):
    """Raised when the checked-in harness configuration is invalid."""


def repository_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_PATH).is_file():
            return candidate
    raise HarnessError(f"could not find {CONFIG_PATH} from {current}")


def load_config(root: Path) -> dict[str, Any]:
    try:
        config = json.loads((root / CONFIG_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read {CONFIG_PATH}: {exc}") from exc
    validate_config(config, root)
    return config


def load_backlog(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    recover_lifecycle_transaction(root)
    relative_path = config.get("backlog")
    if not isinstance(relative_path, str):
        raise HarnessError("'backlog' must point to a backlog JSON file")
    try:
        backlog = json.loads((root / relative_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"cannot read {relative_path}: {exc}") from exc
    validate_backlog(backlog, root)
    return backlog


def validate_backlog(backlog: dict[str, Any], root: Path) -> None:
    if backlog.get("version") != 1 or not isinstance(backlog.get("items"), list):
        raise HarnessError("backlog must have version 1 and an 'items' list")
    seen: set[str] = set()
    for item in backlog["items"]:
        if not isinstance(item, dict):
            raise HarnessError("backlog items must be objects")
        item_id = item.get("id")
        prd = item.get("prd")
        if not isinstance(item_id, str) or item_id in seen:
            raise HarnessError(f"invalid or duplicate backlog id: {item_id!r}")
        if item.get("status") not in BACKLOG_STATUSES:
            raise HarnessError(f"invalid status for {item_id}")
        if not isinstance(item.get("priority"), int):
            raise HarnessError(f"invalid priority for {item_id}")
        if not isinstance(prd, str) or not (root / prd).is_file():
            raise HarnessError(f"PRD does not exist for {item_id}: {prd!r}")
        dependencies = item.get("depends_on")
        if not isinstance(dependencies, list) or not all(
            isinstance(dependency, str) for dependency in dependencies
        ):
            raise HarnessError(f"invalid dependencies for {item_id}")
        validate_prd(item, (root / prd).read_text(encoding="utf-8"))
        seen.add(item_id)
    for item in backlog["items"]:
        unknown = set(item["depends_on"]) - seen
        if unknown:
            raise HarnessError(f"unknown dependencies for {item['id']}: {sorted(unknown)}")


def validate_prd(item: dict[str, Any], text: str) -> None:
    item_id = item["id"]
    header = re.search(r"^# ([A-Z]+-\d+) — (.+)$", text, re.MULTILINE)
    if not header or header.group(1) != item_id or header.group(2) != item["title"]:
        raise HarnessError(f"PRD heading does not match backlog item {item_id}")

    metadata: dict[str, str] = {}
    for key in ("Status", "Priority", "Dependencies"):
        match = re.search(rf"^{key}:\s*(.+?)\s*$", text, re.MULTILINE)
        if not match:
            raise HarnessError(f"PRD {item_id} is missing {key}")
        metadata[key] = match.group(1).strip()

    if metadata["Status"].lower().replace(" ", "_") != item["status"]:
        raise HarnessError(f"PRD status does not match backlog item {item_id}")
    if metadata["Priority"] != str(item["priority"]):
        raise HarnessError(f"PRD priority does not match backlog item {item_id}")
    expected_dependencies = (
        ", ".join(item["depends_on"]) if item["depends_on"] else "None"
    )
    if metadata["Dependencies"] != expected_dependencies:
        raise HarnessError(f"PRD dependencies do not match backlog item {item_id}")

    headings = re.findall(r"^## (.+?)\s*$", text, re.MULTILINE)
    missing = [section for section in PRD_SECTIONS if section not in headings]
    if missing:
        raise HarnessError(f"PRD {item_id} is missing sections: {', '.join(missing)}")

    checklist_states: dict[str, list[str]] = {}
    for index, section in enumerate(PRD_SECTIONS):
        if section not in {"Acceptance criteria", "Ralph slices"}:
            continue
        start = text.index(f"## {section}") + len(f"## {section}")
        following = [
            text.find(f"## {heading}", start)
            for heading in PRD_SECTIONS[index + 1:]
            if text.find(f"## {heading}", start) >= 0
        ]
        end = min(following) if following else len(text)
        states = re.findall(r"^- \[([ xX])\]\s+.+$", text[start:end], re.MULTILINE)
        if not states:
            raise HarnessError(f"PRD {item_id} has no {section.lower()} checklist")
        checklist_states[section] = states

    if item["status"] == "done" and any(
        state == " " for states in checklist_states.values() for state in states
    ):
        raise HarnessError(f"done PRD {item_id} has unchecked work")


def show_backlog(backlog: dict[str, Any], status: str | None = None) -> int:
    items = sorted(backlog["items"], key=lambda item: item["priority"])
    if status:
        items = [item for item in items if item["status"] == status]
    for item in items:
        dependencies = ", ".join(item["depends_on"]) or "-"
        print(
            f"{item['id']}  P{item['priority']:02d}  {item['status']:<11} "
            f"{item['title']}  [depends: {dependencies}]"
        )
    return 0


def show_ralph_prompt(
    root: Path, backlog: dict[str, Any], item_id: str | None
) -> int:
    items = sorted(backlog["items"], key=lambda item: item["priority"])
    statuses = {item["id"]: item["status"] for item in items}

    def unmet_dependencies(item: dict[str, Any]) -> list[str]:
        return [
            dependency
            for dependency in item["depends_on"]
            if statuses[dependency] != "done"
        ]

    if item_id:
        matches = [item for item in items if item["id"] == item_id]
        if not matches:
            raise HarnessError(f"unknown PRD id: {item_id}")
        item = matches[0]
        if item["status"] != "ready":
            raise HarnessError(f"{item_id} is not ready (status: {item['status']})")
        unmet = unmet_dependencies(item)
        if unmet:
            raise HarnessError(
                f"{item_id} has unmet dependencies: {', '.join(unmet)}"
            )
    else:
        candidates = [
            item
            for item in items
            if item["status"] == "ready" and not unmet_dependencies(item)
        ]
        if not candidates:
            raise HarnessError("no executable PRDs in backlog")
        item = candidates[0]
    prd_text = (root / item["prd"]).read_text(encoding="utf-8").rstrip()
    print(
        "You are executing one Ralph iteration for "
        f"{item['id']}: {item['title']}.\n\n"
        "Read AGENTS.md and the PRD below. Inspect the current repository state. "
        "Select exactly one smallest unchecked slice whose dependencies are met. "
        "Implement it completely, add or update tests, run the relevant harness "
        "checks, and update only factual progress in the PRD. Do not start a second "
        "slice. If blocked, record the evidence and the required unblock action.\n\n"
        f"{prd_text}"
    )
    return 0


def ralph_status(backlog: dict[str, Any], as_json: bool = False) -> int:
    items = sorted(backlog["items"], key=lambda item: item["priority"])
    statuses = {item["id"]: item["status"] for item in items}
    records = []
    for item in items:
        unmet = [
            dependency
            for dependency in item["depends_on"]
            if statuses[dependency] != "done"
        ]
        records.append(
            {
                "id": item["id"],
                "title": item["title"],
                "priority": item["priority"],
                "status": item["status"],
                "depends_on": item["depends_on"],
                "unmet_dependencies": unmet,
                "executable": item["status"] == "ready" and not unmet,
            }
        )
    if as_json:
        print(json.dumps({"schema_version": 1, "items": records}, indent=2))
        return 0
    for record in records:
        unmet = ", ".join(record["unmet_dependencies"]) or "-"
        executable = "yes" if record["executable"] else "no"
        print(
            f"{record['id']}  {record['status']:<11} "
            f"executable={executable:<3}  unmet={unmet}"
        )
    return 0


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _transaction_target(root: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        raise HarnessError("lifecycle transaction target must be relative")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise HarnessError("lifecycle transaction target escapes repository") from exc
    return resolved


def recover_lifecycle_transaction(root: Path) -> None:
    journal_path = root / TRANSACTION_PATH
    if not journal_path.exists():
        return
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if journal.get("schema_version") != 1:
            raise ValueError("unsupported schema version")
        changes = journal["changes"]
        if not isinstance(changes, list) or not changes:
            raise ValueError("missing changes")
        targets = [
            (
                _transaction_target(root, change["path"]),
                change["before"],
                change["after"],
            )
            for change in changes
        ]
        if not all(
            isinstance(before, str) and isinstance(after, str)
            for _, before, after in targets
        ):
            raise ValueError("invalid change content")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise HarnessError(f"invalid lifecycle transaction: {exc}") from exc

    committed = all(
        path.is_file() and path.read_text(encoding="utf-8") == after
        for path, _, after in targets
    )
    if not committed:
        for path, before, _ in targets:
            atomic_write_text(path, before)
    journal_path.unlink()


def _write_lifecycle_transaction(
    root: Path, changes: list[tuple[Path, str, str]]
) -> None:
    journal = {
        "schema_version": 1,
        "changes": [
            {
                "path": path.relative_to(root).as_posix(),
                "before": before,
                "after": after,
            }
            for path, before, after in changes
        ],
    }
    journal_path = root / TRANSACTION_PATH
    atomic_write_text(journal_path, json.dumps(journal, indent=2) + "\n")
    for path, _, after in changes:
        atomic_write_text(path, after)
    journal_path.unlink()


def _transition_item(
    root: Path,
    config: dict[str, Any],
    backlog: dict[str, Any],
    item: dict[str, Any],
    new_status: str,
    dry_run: bool,
) -> int:
    prd_path = root / item["prd"]
    prd_text = prd_path.read_text(encoding="utf-8")
    updated_prd = re.sub(
        r"^Status:.*$",
        f"Status: {PRD_STATUS_LABELS[new_status]}  ",
        prd_text,
        count=1,
        flags=re.MULTILINE,
    )
    updated_backlog = json.loads(json.dumps(backlog))
    updated_item = next(
        candidate for candidate in updated_backlog["items"]
        if candidate["id"] == item["id"]
    )
    updated_item["status"] = new_status
    validate_prd(updated_item, updated_prd)
    action = f"{item['id']}: {item['status']} -> {new_status}"
    if dry_run:
        print(f"DRY RUN {action}")
        return 0
    backlog_path = root / config["backlog"]
    updated_backlog_text = json.dumps(updated_backlog, indent=2) + "\n"
    _write_lifecycle_transaction(
        root,
        [
            (prd_path, prd_text, updated_prd),
            (
                backlog_path,
                backlog_path.read_text(encoding="utf-8"),
                updated_backlog_text,
            ),
        ],
    )
    print(action)
    return 0


def ralph_start(
    root: Path,
    config: dict[str, Any],
    backlog: dict[str, Any],
    item_id: str | None,
    dry_run: bool = False,
    allow_concurrent: bool = False,
) -> int:
    items = sorted(backlog["items"], key=lambda item: item["priority"])
    statuses = {item["id"]: item["status"] for item in items}
    active = [item["id"] for item in items if item["status"] == "in_progress"]
    if active and not allow_concurrent:
        raise HarnessError(f"already in progress: {', '.join(active)}")
    executable = [
        item for item in items
        if item["status"] == "ready"
        and all(statuses[dependency] == "done" for dependency in item["depends_on"])
    ]
    if item_id is not None:
        matches = [item for item in items if item["id"] == item_id]
        if not matches:
            raise HarnessError(f"unknown PRD id: {item_id}")
        item = matches[0]
        if item not in executable:
            raise HarnessError(f"{item_id} is not executable")
    elif executable:
        item = executable[0]
    else:
        raise HarnessError("no executable PRDs in backlog")
    return _transition_item(root, config, backlog, item, "in_progress", dry_run)


def ralph_complete(
    root: Path,
    config: dict[str, Any],
    backlog: dict[str, Any],
    item_id: str | None,
    dry_run: bool = False,
) -> int:
    active = [item for item in backlog["items"] if item["status"] == "in_progress"]
    if item_id is not None:
        active = [item for item in active if item["id"] == item_id]
    if not active:
        raise HarnessError("no matching PRD is in progress")
    if len(active) > 1:
        raise HarnessError("multiple PRDs are in progress; specify an id")
    item = active[0]
    text = (root / item["prd"]).read_text(encoding="utf-8")
    states = re.findall(r"^- \[([ xX])\]\s+.+$", text, re.MULTILINE)
    if not states or any(state == " " for state in states):
        raise HarnessError(f"{item['id']} has unchecked work")
    receipt_path = root / RECEIPT_DIRECTORY / f"{item['id']}.json"
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessError(f"{item['id']} has no valid verification receipt") from exc
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if (
        receipt.get("schema_version") != 1
        or receipt.get("item_id") != item["id"]
        or receipt.get("result") != "passed"
        or receipt.get("prd_sha256") != digest
    ):
        raise HarnessError(f"{item['id']} verification receipt is stale or failed")
    return _transition_item(root, config, backlog, item, "done", dry_run)


def ralph_verify(
    root: Path,
    config: dict[str, Any],
    backlog: dict[str, Any],
    item_id: str,
    scope: str,
    dry_run: bool = False,
) -> int:
    matches = [item for item in backlog["items"] if item["id"] == item_id]
    if not matches:
        raise HarnessError(f"unknown PRD id: {item_id}")
    item = matches[0]
    if item["status"] != "in_progress":
        raise HarnessError(f"{item_id} is not in progress")
    if dry_run:
        print(f"DRY RUN verify {item_id} with scope {scope}")
        return 0
    result = run_checks(root, config, scope)
    if result:
        raise HarnessError(f"verification failed for {item_id}")
    prd_text = (root / item["prd"]).read_text(encoding="utf-8")
    receipt = {
        "schema_version": 1,
        "item_id": item_id,
        "result": "passed",
        "scope": scope,
        "prd_sha256": hashlib.sha256(prd_text.encode("utf-8")).hexdigest(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    receipt_path = root / RECEIPT_DIRECTORY / f"{item_id}.json"
    atomic_write_text(receipt_path, json.dumps(receipt, indent=2) + "\n")
    print(f"Recorded passing verification for {item_id}: {receipt_path}")
    return 0


def validate_config(config: dict[str, Any], root: Path) -> None:
    if config.get("version") != 1:
        raise HarnessError("ai/harness.json must have version 1")
    context = config.get("context")
    scopes = config.get("scopes")
    if not isinstance(context, list) or not context:
        raise HarnessError("'context' must be a non-empty list")
    if not isinstance(scopes, dict) or not scopes:
        raise HarnessError("'scopes' must be a non-empty object")
    for relative_path in context:
        if not isinstance(relative_path, str) or not (root / relative_path).is_file():
            raise HarnessError(f"context file does not exist: {relative_path!r}")
    for scope, gates in scopes.items():
        if not isinstance(gates, list) or not gates:
            raise HarnessError(f"scope {scope!r} must contain at least one gate")
        for gate in gates:
            command = gate.get("command") if isinstance(gate, dict) else None
            if (
                not isinstance(gate, dict)
                or not isinstance(gate.get("name"), str)
                or not isinstance(command, list)
                or not command
                or not all(isinstance(part, str) and part for part in command)
            ):
                raise HarnessError(f"invalid gate in scope {scope!r}")


def show_context(root: Path, config: dict[str, Any], full: bool) -> int:
    for relative_path in config["context"]:
        print(f"\n## {relative_path}")
        if full:
            print((root / relative_path).read_text(encoding="utf-8").rstrip())
    return 0


def run_checks(root: Path, config: dict[str, Any], scope: str) -> int:
    selected = (
        list(config["scopes"])
        if scope == "all"
        else [scope]
    )
    for scope_name in selected:
        for gate in config["scopes"][scope_name]:
            print(f"\n[{scope_name}] {gate['name']}", flush=True)
            command = [
                sys.executable if part == "{python}" else part
                for part in gate["command"]
            ]
            result = subprocess.run(command, cwd=root, check=False)
            if result.returncode:
                print(f"FAILED ({result.returncode}): {gate['name']}", file=sys.stderr)
                return result.returncode
    print("\nAll selected checks passed.")
    return 0


def build_parser(scopes: Sequence[str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness",
        description="Inspect and validate the repository's AI development harness.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    context_parser = subparsers.add_parser("context", help="show durable agent context")
    context_parser.add_argument(
        "--full", action="store_true", help="print file contents as well as paths"
    )
    subparsers.add_parser("doctor", help="validate harness files and configuration")
    check_parser = subparsers.add_parser("check", help="run configured quality gates")
    check_parser.add_argument(
        "--scope", choices=["all", *scopes], default="all", help="gate scope to run"
    )
    backlog_parser = subparsers.add_parser("backlog", help="list improvement PRDs")
    backlog_parser.add_argument("--status", choices=sorted(BACKLOG_STATUSES))
    ralph_parser = subparsers.add_parser(
        "ralph", help="print a bounded prompt for one Ralph iteration"
    )
    ralph_parser.add_argument("--prd", metavar="ID", help="PRD id; defaults to first ready")
    ralph_commands = ralph_parser.add_subparsers(dest="ralph_command")
    status_parser = ralph_commands.add_parser(
        "status", help="show read-only Ralph lifecycle state"
    )
    status_parser.add_argument(
        "--json", action="store_true", help="emit schema-versioned JSON"
    )
    start_parser = ralph_commands.add_parser(
        "start", help="claim one executable PRD"
    )
    start_parser.add_argument("item_id", nargs="?", metavar="ID")
    start_parser.add_argument("--dry-run", action="store_true")
    start_parser.add_argument(
        "--allow-concurrent",
        action="store_true",
        help="allow another PRD to remain in progress",
    )
    complete_parser = ralph_commands.add_parser(
        "complete", help="complete one in-progress PRD"
    )
    complete_parser.add_argument("item_id", nargs="?", metavar="ID")
    complete_parser.add_argument("--dry-run", action="store_true")
    verify_parser = ralph_commands.add_parser(
        "verify", help="run configured checks and record a passing receipt"
    )
    verify_parser.add_argument("item_id", metavar="ID")
    verify_parser.add_argument(
        "--scope", choices=["all", *scopes], default="all"
    )
    verify_parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    try:
        root = repository_root()
        config = load_config(root)
        backlog = load_backlog(root, config)
        args = build_parser(list(config["scopes"])).parse_args(argv)
        if args.command == "context":
            return show_context(root, config, args.full)
        if args.command == "doctor":
            print(f"Harness is valid: {root}")
            return 0
        if args.command == "backlog":
            return show_backlog(backlog, args.status)
        if args.command == "ralph":
            if args.ralph_command == "status":
                return ralph_status(backlog, args.json)
            if args.ralph_command == "start":
                return ralph_start(
                    root,
                    config,
                    backlog,
                    args.item_id,
                    args.dry_run,
                    args.allow_concurrent,
                )
            if args.ralph_command == "complete":
                return ralph_complete(
                    root, config, backlog, args.item_id, args.dry_run
                )
            if args.ralph_command == "verify":
                return ralph_verify(
                    root,
                    config,
                    backlog,
                    args.item_id,
                    args.scope,
                    args.dry_run,
                )
            return show_ralph_prompt(root, backlog, args.prd)
        return run_checks(root, config, args.scope)
    except HarnessError as exc:
        print(f"harness error: {exc}", file=sys.stderr)
        return 2
