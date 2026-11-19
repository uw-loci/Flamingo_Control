"""Post-update verification from the command line.

Run from the Flamingo_Control folder (it reads ``microscope_settings/`` and
``workflows/`` there), on a computer on the instrument network:

    python -m py2flamingo.verification --host 10.129.37.20 --tiers T0,T1
    python -m py2flamingo.verification --host 10.129.37.20 --tiers T4 \\
        --sample "bead slide" --confirm-motion --confirm-light
    python -m py2flamingo.verification --host 10.129.37.20 --dry-run
    python -m py2flamingo.verification --compare verification/A verification/B

Tiers are cumulative (T2 runs T0-T2). T2 and above ask for confirmation
unless given with the --confirm flags, and need a sample or target mounted
(--sample). Ctrl+C stops at once: halt, lasers and LED off, live view and
workflow stopped; settings are restored, the stage is not moved back.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from py2flamingo.verification import report  # noqa: E402
from py2flamingo.verification.context import RunOptions  # noqa: E402
from py2flamingo.verification.model import Outcome, Tier, checks_for  # noqa: E402
from py2flamingo.verification.runner import RunCallbacks  # noqa: E402


def _parse_tiers(text: str) -> Tier:
    tiers = [
        Tier(int(t.strip().upper().lstrip("T"))) for t in text.split(",") if t.strip()
    ]
    return max(tiers)


def _ask(prompt: str) -> bool:
    try:
        return input(f"{prompt} Type yes to continue: ").strip().lower() == "yes"
    except EOFError:
        return False


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m py2flamingo.verification",
        description="Check every command Flamingo_Control uses against a microscope.",
    )
    p.add_argument("--host", help="microscope control computer address")
    p.add_argument(
        "--port", type=int, default=53717, help="command port (live view is port + 1)"
    )
    p.add_argument(
        "--tiers",
        default="T0",
        help="highest tier to run, e.g. T1 or T0,T1 (cumulative)",
    )
    p.add_argument("--only", help="comma-separated check ids to run (within the tiers)")
    p.add_argument(
        "--sample", default="", help="what is mounted (required for T2 and above)"
    )
    p.add_argument(
        "--target",
        help="target name from verification_targets in the rig's settings json",
    )
    p.add_argument(
        "--out", type=Path, default=Path("verification"), help="report folder root"
    )
    p.add_argument(
        "--save-drive",
        default="",
        help="server drive for T4 data (default: first listed)",
    )
    p.add_argument("--confirm-motion", action="store_true")
    p.add_argument("--confirm-light", action="store_true")
    p.add_argument("--confirm-acquisition", action="store_true")
    p.add_argument(
        "--dry-run", action="store_true", help="list what would be sent; send nothing"
    )
    p.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        help="compare two run folders",
    )
    p.add_argument("--log-level", default="WARNING")
    args = p.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))

    if args.compare:
        print(
            report.compare(
                report.load(Path(args.compare[0])), report.load(Path(args.compare[1]))
            )
        )
        return 0

    top = _parse_tiers(args.tiers)
    options = RunOptions(sample=args.sample, server_save_drive=args.save_drive)
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    tiers = [t for t in Tier if t <= top]

    if args.dry_run:
        for c in checks_for(tiers, only):
            needs = f"  (after {', '.join(c.after)})" if c.after else ""
            print(f"{c.tier.label} {c.check_id:<24} {c.title}{needs}")
            for s in c.sends:
                print(f"      sends {s}")
        return 0

    if not args.host:
        p.error("--host is required (or --dry-run / --compare)")
    if top >= Tier.MOTION and not args.sample:
        p.error("T2 and above need --sample naming the test sample or target mounted")

    from py2flamingo.verification import session

    confirmations = []
    flags = {
        Tier.MOTION: args.confirm_motion,
        Tier.LIGHT: args.confirm_light,
        Tier.ACQUISITION: args.confirm_acquisition,
    }
    for tier in (Tier.MOTION, Tier.LIGHT, Tier.ACQUISITION):
        if top < tier:
            continue
        text = session.confirmation_text(tier, options)
        if not (flags[tier] or _ask(f"{tier.label}: {text}")):
            print(f"Not confirmed: running up to T{int(tier) - 1}.")
            top = Tier(int(tier) - 1)
            break
        confirmations.append(f"{tier.label}: {text}")
    tiers = [t for t in Tier if t <= top]

    from py2flamingo.core.tcp_connection import TCPConnection
    from py2flamingo.core.tcp_protocol import ProtocolEncoder
    from py2flamingo.models.connection import ConnectionConfig
    from py2flamingo.services.connection_service import MVCConnectionService

    connection = MVCConnectionService(TCPConnection(), ProtocolEncoder())
    print(f"Connecting to {args.host}:{args.port} ...")
    connection.connect(ConnectionConfig(args.host, args.port, args.port + 1))
    abort = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: (print("\nAborting ..."), abort.set()))
    try:
        prepared = session.prepare(connection)
        for note in prepared.notes:
            print(f"Note: {note}")
        origin = {}
        if top >= Tier.MOTION:
            from py2flamingo.services.stage_service import StageService

            stage = StageService(connection)
            origin = {
                a: stage.get_axis_position(i)
                for a, i in (("x", 1), ("y", 2), ("z", 3), ("r", 4))
            }
        target = (
            session.target_for(prepared, args.target, origin)
            if top >= Tier.MOTION
            else None
        )
        n_total = [0]

        def started(i, n, chk):
            n_total[0] = n

        def finished(i, n, result):
            print(
                f"[{i:>2}/{n}] {result.outcome.value:<8} {result.check_id:<24} {result.title}"
            )
            if result.outcome != Outcome.PASS and result.detail:
                print(f"        {result.detail}")

        summary, folder = session.execute(
            prepared,
            tiers,
            options,
            target=target,
            report_root=args.out,
            confirmations=confirmations,
            callbacks=RunCallbacks(check_started=started, check_finished=finished),
            abort=abort,
            only=only,
        )
        for r in summary.results[n_total[0] :]:
            print(f"        {r.outcome.value:<8} {r.title}: {r.detail}")
        counts = ", ".join(
            f"{summary.count(o)} {o.value}" for o in Outcome if summary.count(o)
        )
        print(f"\n{counts}\nReport: {folder / 'report.txt'}")
        return 0 if summary.ok else 1
    finally:
        try:
            connection.disconnect()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
