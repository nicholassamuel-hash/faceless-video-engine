"""Human-in-the-loop review CLI.

List generated videos, preview them in the default player, and approve/reject.
Only entries set to ``approved`` here become eligible for upload.

Usable directly:
    python -m approval.review_cli list
    python -m approval.review_cli preview 3
    python -m approval.review_cli approve 3
    python -m approval.review_cli reject 3 --reason "audio clipped"
    python -m approval.review_cli interactive
"""
from __future__ import annotations

import argparse
import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

from faceless_engine.config import get_settings
from shared import db
from shared.logging_setup import setup_logging
from shared.models import QueueStatus

log = logging.getLogger(__name__)


def open_in_player(video_path: str) -> None:
    """Open the video with the OS default player (best-effort, non-blocking)."""
    p = Path(video_path)
    if not p.exists():
        raise FileNotFoundError(f"video not found: {p}")
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
    except Exception:
        log.exception("Could not open preview for %s", p)
        raise


def _print_entry(e: dict, *, full: bool = False) -> None:
    print(f"[{e['queue_id']:>4}] {e['status']:<9} | {e['title']}")
    print(f"        file: {e['video_path']}")
    if full:
        print(f"        desc: {e['description']}")
        print(f"        tags: {e['hashtags']}")
        print(f"        caption: {e['caption']}")
        if e.get("reject_reason"):
            print(f"        reject reason: {e['reject_reason']}")


def cmd_list(args: argparse.Namespace) -> int:
    status = QueueStatus(args.status) if args.status else QueueStatus.generated
    entries = db.list_queue(status=status)
    if not entries:
        print(f"No entries with status '{status.value}'.")
        return 0
    print(f"== Queue ({status.value}): {len(entries)} ==")
    for e in entries:
        _print_entry(e, full=args.verbose)
    return 0


def cmd_preview(args: argparse.Namespace) -> int:
    e = db.get_queue_entry(args.id)
    if not e:
        print(f"Queue entry {args.id} not found.", file=sys.stderr)
        return 1
    _print_entry(e, full=True)
    open_in_player(e["video_path"])
    print("Opened in default player.")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    e = db.get_queue_entry(args.id)
    if not e:
        print(f"Queue entry {args.id} not found.", file=sys.stderr)
        return 1
    db.set_queue_status(args.id, QueueStatus.approved)
    print(f"Approved queue entry {args.id}: {e['title']}")
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    e = db.get_queue_entry(args.id)
    if not e:
        print(f"Queue entry {args.id} not found.", file=sys.stderr)
        return 1
    db.set_queue_status(args.id, QueueStatus.rejected, reason=args.reason)
    print(f"Rejected queue entry {args.id} ({args.reason}): {e['title']}")
    return 0


def cmd_interactive(args: argparse.Namespace) -> int:
    """Walk through each generated entry: preview, then approve/reject/skip."""
    entries = db.list_queue(status=QueueStatus.generated)
    if not entries:
        print("Nothing to review.")
        return 0
    for e in entries:
        print("\n" + "=" * 60)
        _print_entry(e, full=True)
        try:
            open_in_player(e["video_path"])
        except Exception as exc:  # preview failure shouldn't block decisions
            print(f"(preview failed: {exc})")
        while True:
            choice = input("[a]pprove / [r]eject / [s]kip / [q]uit > ").strip().lower()
            if choice in ("a", "approve"):
                db.set_queue_status(e["queue_id"], QueueStatus.approved)
                print("  -> approved")
                break
            if choice in ("r", "reject"):
                reason = input("  reason: ").strip() or "no reason given"
                db.set_queue_status(e["queue_id"], QueueStatus.rejected, reason=reason)
                print("  -> rejected")
                break
            if choice in ("s", "skip", ""):
                print("  -> skipped")
                break
            if choice in ("q", "quit"):
                return 0
            print("  (please choose a/r/s/q)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="review", description="Review generated videos.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list queue entries")
    p_list.add_argument(
        "--status", choices=[s.value for s in QueueStatus], default=None,
        help="filter by status (default: generated)",
    )
    p_list.add_argument("-v", "--verbose", action="store_true")
    p_list.set_defaults(func=cmd_list)

    p_prev = sub.add_parser("preview", help="open a video in the default player")
    p_prev.add_argument("id", type=int)
    p_prev.set_defaults(func=cmd_preview)

    p_app = sub.add_parser("approve", help="approve an entry for upload")
    p_app.add_argument("id", type=int)
    p_app.set_defaults(func=cmd_approve)

    p_rej = sub.add_parser("reject", help="reject an entry")
    p_rej.add_argument("id", type=int)
    p_rej.add_argument("--reason", default="no reason given")
    p_rej.set_defaults(func=cmd_reject)

    p_int = sub.add_parser("interactive", help="step through all generated entries")
    p_int.set_defaults(func=cmd_interactive)
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    db.init_db(get_settings())
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
