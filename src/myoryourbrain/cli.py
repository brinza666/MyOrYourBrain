from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .council import CouncilRunner
from .storage import BrainStore
from .types import BrainError, Evidence


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))


def _load_evidence(path: str | Path) -> list[Evidence]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BrainError(f"cannot load evidence {path}: {exc}") from exc
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise BrainError("evidence file must contain an array of objects")
    return [Evidence.from_dict(item) for item in raw]


def _text_argument(args: argparse.Namespace) -> str:
    if args.text is not None and args.file is not None:
        raise BrainError("use either --text or --file, not both")
    if args.file is not None:
        try:
            return Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            raise BrainError(f"cannot read {args.file}: {exc}") from exc
    if args.text is None:
        raise BrainError("--text or --file is required")
    return args.text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="brain", description="Git-backed, provider-neutral agent memory")
    parser.add_argument("--root", default=".", help="brain repository root (default: current directory)")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="create safe local/public memory directories")

    capture = commands.add_parser("capture", help="store a public or private Markdown memory")
    capture.add_argument("--title", required=True)
    capture.add_argument("--text")
    capture.add_argument("--file")
    capture.add_argument("--scope", choices=("public", "private"), default="private")
    capture.add_argument("--tag", action="append", default=[])

    search = commands.add_parser("search", help="exact local search without SQLite or network")
    search.add_argument("query")
    search.add_argument("--include-private", action="store_true")
    search.add_argument("--limit", type=int, default=20)

    index = commands.add_parser("index", help="build a disposable JSON search index")
    index.add_argument("--include-private", action="store_true")

    commands.add_parser("doctor", help="check memory structure and public secret boundary")
    commands.add_parser("validate", help="alias for doctor")

    council = commands.add_parser("council", help="run a bounded evidence-gated council")
    council.add_argument("--goal", required=True)
    council.add_argument("--criterion", action="append", required=True)
    council.add_argument("--risk", choices=("low", "medium", "high"), default="medium")
    council.add_argument("--evidence", required=True)
    council.add_argument("--providers", required=True)
    council.add_argument("--config")
    council.add_argument(
        "--allow-command-providers",
        action="store_true",
        help="explicitly authorize configured local executables (privileged; not sandboxed)",
    )
    council.add_argument("--run-id")

    inspect = commands.add_parser("inspect", help="show an active or archived council outcome")
    inspect.add_argument("run_id")

    approve = commands.add_parser("approve", help="record a local human attestation bound to the run hash")
    approve.add_argument("run_id")
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--scope", choices=("promote",), default="promote")
    approve.add_argument("--confirm", action="store_true", help="confirm that the run was inspected")

    promote = commands.add_parser("promote", help="record an accepted outcome in the Git-visible evolution ledger")
    promote.add_argument("run_id")
    promote.add_argument("--approved-by", required=True)

    reset = commands.add_parser("reset", help="archive runtime runs without deleting durable memory")
    reset.add_argument("--run-id")

    export = commands.add_parser("export-public", help="create a secret-scanned export without Git history")
    export.add_argument("--dest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = BrainStore(args.root)
    try:
        if args.command == "init":
            _print(store.initialize())
        elif args.command == "capture":
            store.initialize()
            note = store.capture(args.title, _text_argument(args), scope=args.scope, tags=args.tag)
            _print(note.to_dict())
        elif args.command == "search":
            _print(store.search(args.query, include_private=args.include_private, limit=args.limit))
        elif args.command == "index":
            _print(store.build_index(include_private=args.include_private))
        elif args.command in {"doctor", "validate"}:
            _print(store.validate())
        elif args.command == "council":
            runner = CouncilRunner.from_files(
                store.root,
                providers_path=args.providers,
                config_path=args.config,
                allow_command_providers=args.allow_command_providers,
            )
            outcome = runner.run(
                args.goal,
                acceptance_criteria=args.criterion,
                risk=args.risk,
                evidence=_load_evidence(args.evidence),
                run_id=args.run_id,
            )
            _print(outcome.to_dict())
        elif args.command == "inspect":
            _print(store.load_run(args.run_id))
        elif args.command == "approve":
            _print(
                {
                    "path": str(
                        store.approve_run(
                            args.run_id,
                            approved_by=args.approved_by,
                            scope=args.scope,
                            confirmed=args.confirm,
                        )
                    ),
                    "identity_authenticated": False,
                }
            )
        elif args.command == "promote":
            _print({"path": str(store.promote(args.run_id, approved_by=args.approved_by))})
        elif args.command == "reset":
            _print({"archived": [str(path) for path in store.reset(args.run_id)]})
        elif args.command == "export-public":
            _print(store.export_public(args.dest))
        else:
            parser.error(f"unsupported command: {args.command}")
    except BrainError as exc:
        print(f"brain: {exc}", file=sys.stderr)
        return 2
    return 0
