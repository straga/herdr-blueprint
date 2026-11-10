"""Command line: run the viewer, or send something to a running viewer."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: no lock, the race stays as before
    fcntl = None

from .item import VIEWABLE_SUFFIXES
from .sources import inbox, pane

PLUGIN_ID = pane.PLUGIN_ID

DRAW_HELP = "draw Mermaid diagrams (slower; without it they stay as text)"


def focused_pane_id(herdr: str, pane_id: str | None, run=subprocess.run) -> str | None:
    """`pane_id`, or the focused pane's id when it is None (None if herdr can't tell)."""
    if pane_id:
        return pane_id
    try:
        # herdr prints UTF-8; Windows would otherwise decode with the ANSI code page.
        result = run([herdr, "pane", "current"], capture_output=True, encoding="utf-8", errors="replace")
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)["result"]["pane"]["pane_id"]
    except (ValueError, KeyError, TypeError):
        return None


# Set on the viewer by `open`: the pane it was opened next to.
SOURCE_ENV = "BLUEPRINT_SOURCE_PANE"


def open_command(herdr: str, pane_id: str | None, focus: bool = True) -> list[str]:
    """herdr command that opens the viewer to the right of `pane_id`, focusing it unless told not to."""
    cmd = [
        herdr, "plugin", "pane", "open",
        "--plugin", PLUGIN_ID, "--entrypoint", "viewer",
        "--placement", "split", "--direction", "right",
    ]
    if focus:
        cmd.append("--focus")
    if pane_id:
        cmd += ["--target-pane", pane_id, "--env", f"{SOURCE_ENV}={pane_id}"]
    return cmd


def open_viewer(herdr: str, pane_id: str | None, run=subprocess.run, exists=pane.pane_exists) -> int:
    """Focus the viewer already open next to `pane_id`, or open one there. Outside a pane, the
    viewer with no neighbour."""
    with _pane_lock(pane_id) if pane_id else contextlib.nullcontext():
        for existing in inbox.viewer_panes(owner=pane_id):
            if not exists(herdr, existing):
                inbox.clear_viewer(existing, owner=pane_id)  # its pane was closed without cleanup
                continue
            # Fails for a viewer run by hand in a shell pane: that is not a plugin pane.
            if run([herdr, "plugin", "pane", "focus", existing]).returncode == 0:
                return 0
        if not pane_id:
            return run(open_command(herdr, pane_id)).returncode
        return 0 if _open_recorded(herdr, pane_id, focus=True, run=run) else 1


@contextlib.contextmanager
def _pane_lock(own_pane: str):
    """One opener at a time for the agent pane `own_pane`: `open` and sends both open its viewer."""
    folder = inbox.inbox_dir(owner=own_pane)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / ".open.lock", "w") as lock:
        if fcntl:
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _open_recorded(herdr: str, own_pane: str, focus: bool, run) -> bool:
    """Open the viewer of `own_pane` and record it for the pane at once. A viewer records itself
    only when it has started, a second later; an opener that left that to the viewer let the next
    send see no viewer and open a second one."""
    try:
        done = run(open_command(herdr, own_pane, focus=focus), capture_output=True, text=True)
    except OSError:
        return False
    if done.returncode != 0:
        return False
    viewer = _opened_pane_id(done.stdout)
    if viewer:
        inbox.mark_viewer(viewer, owner=own_pane)
    return True


def sender_opening_own_viewer(herdr: str, own_pane: str | None, sender: str | None) -> str | None:
    """The inbox a send goes to. An agent in a herdr pane without a viewer of its own gets one opened
    beside it, without focus, and its send waits in that viewer's inbox until the viewer starts: an
    agent that skips `open` would otherwise send to the workspace's inbox, unseen next to it. If
    the viewer cannot be opened, the send goes to the workspace's inbox, as before."""
    if sender or not own_pane:
        return sender
    with _pane_lock(own_pane):
        if own_inbox(herdr, own_pane):  # opened by `open` or another send while this one waited
            return own_pane
        return own_pane if _open_recorded(herdr, own_pane, focus=False, run=subprocess.run) else None


def _opened_pane_id(stdout: str | None) -> str | None:
    """The pane id in herdr's answer to `plugin pane open`, or None when it cannot be read."""
    try:
        return json.loads(stdout or "")["result"]["plugin_pane"]["pane"]["pane_id"]
    except (ValueError, KeyError, TypeError):
        return None


def own_inbox(herdr: str, own_pane: str | None, exists=None) -> str | None:
    """The agent pane whose inbox an agent in `own_pane` writes to and asks about: its own while a
    viewer of its own is open; None — the workspace's, as before. A record left by a viewer closed
    without cleanup is dropped, so sends do not go where no one reads."""
    if not own_pane:
        return None
    exists = exists or pane.pane_exists
    for viewer in inbox.viewer_panes(owner=own_pane):
        if exists(herdr, viewer):
            return own_pane
        inbox.clear_viewer(viewer, owner=own_pane)
    return None


def read_stdin() -> str:
    """stdin as UTF-8 whatever the console code page is."""
    raw = getattr(sys.stdin, "buffer", None)
    if raw is None:
        return sys.stdin.read()
    return raw.read().decode("utf-8-sig", errors="replace")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="herdr-blueprint",
        description="Live Markdown and Mermaid viewer for the herdr side pane.",
    )
    # The metavar lists the commands people use; edit-session runs inside Blueprint's editor pane.
    commands = parser.add_subparsers(
        dest="command", metavar="{show,send,refresh,open,status,install-skill,uninstall-skill}",
    )
    show = commands.add_parser("show", help="show a Markdown or Mermaid file in the viewer")
    show.add_argument("path", type=Path)
    show.add_argument("--draw", action="store_true", help=DRAW_HELP)
    send = commands.add_parser("send", help="send Markdown or Mermaid from stdin to the viewer")
    send.add_argument("--title", default=None, help="label in the header")
    send.add_argument("--draw", action="store_true", help=DRAW_HELP)
    refresh = commands.add_parser("refresh", help="re-read and redraw what the viewer shows")
    refresh.add_argument("--draw", action="store_true", help=DRAW_HELP)
    commands.add_parser("open", help="open the viewer pane in herdr")
    commands.add_parser("status", help="print open or closed: whether a viewer is open (exit 0 when open)")
    commands.add_parser("edit-session")  # no help: left out of the command list
    install_skill = commands.add_parser("install-skill", help="add the Blueprint skill to Claude Code and Codex")
    install_skill.add_argument("--copy", action="store_true", help=argparse.SUPPRESS)  # the plugin build: never fails it
    commands.add_parser("uninstall-skill", help="remove the Blueprint skill")
    return parser


def main(argv: list[str] | None = None) -> int:
    # A narrow console encoding must not turn a sent message into a crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = _parser().parse_args(argv)

    # An agent writes to and asks about the inbox of its own viewer, when it has one.
    sender = None
    if args.command in ("show", "send", "refresh", "status"):
        sender = own_inbox(os.environ.get("HERDR_BIN_PATH", "herdr"), os.environ.get("HERDR_PANE_ID"))

    if args.command == "show":
        path = args.path.expanduser().resolve()
        if not path.is_file():
            print(f"herdr-blueprint: file not found: {path}", file=sys.stderr)
            return 1
        if path.suffix.lower() not in VIEWABLE_SUFFIXES:
            print("herdr-blueprint: only .md, .markdown, .mmd and .mermaid files", file=sys.stderr)
            return 2
        sender = sender_opening_own_viewer(os.environ.get("HERDR_BIN_PATH", "herdr"), os.environ.get("HERDR_PANE_ID"), sender)
        inbox.send({"kind": "file", "path": str(path), "draw": args.draw, "sent_by": "agent"}, owner=sender)
        print(f"Sent to Blueprint: {path.name}")
        return 0

    if args.command == "send":
        source = read_stdin().strip()
        if not source:
            print("herdr-blueprint: nothing on stdin", file=sys.stderr)
            return 2
        sender = sender_opening_own_viewer(os.environ.get("HERDR_BIN_PATH", "herdr"), os.environ.get("HERDR_PANE_ID"), sender)
        inbox.send({"kind": "text", "source": source, "title": args.title, "draw": args.draw, "sent_by": "agent"},
                   owner=sender)
        print(f"Sent to Blueprint: {args.title or 'snippet'}")
        return 0

    if args.command == "refresh":
        inbox.send({"kind": "refresh", "draw": args.draw}, owner=sender)
        print("Asked Blueprint to refresh")
        return 0

    if args.command == "status":
        herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
        viewing = any(pane.pane_exists(herdr, pane_id) for pane_id in inbox.viewer_panes(owner=sender))
        print("open" if viewing else "closed")
        return 0 if viewing else 1

    if args.command in ("install-skill", "uninstall-skill"):
        from . import skills_install

        skill = skills_install.skill_dir()
        if not skill.is_dir():
            print("herdr-blueprint: skill folder not found; run from the plugin folder", file=sys.stderr)
            return 1
        building = getattr(args, "copy", False)
        try:
            if args.command == "install-skill":
                lines = skills_install.install(skill, Path.home(), copy=building)
            else:
                lines = skills_install.uninstall(skill, Path.home())
        except OSError as exc:
            print(f"herdr-blueprint: could not install the skill: {exc}", file=sys.stderr)
            return 0 if building else 1  # a plugin install still succeeds without it
        for line in lines or ["nothing to do"]:
            print(line)
        return 0

    if args.command == "edit-session":
        from .edit import run_session

        return run_session(os.environ)

    herdr = os.environ.get("HERDR_BIN_PATH", "herdr")
    if args.command == "open":
        return open_viewer(herdr, focused_pane_id(herdr, os.environ.get("HERDR_PANE_ID")))

    from .app import BlueprintApp

    # Inside its herdr pane, the viewer records itself so `open` focuses it next time; opened next
    # to an agent pane, as that pane's viewer, reading that pane's inbox.
    viewer_id = os.environ.get("HERDR_PANE_ID")
    source_pane = os.environ.get(SOURCE_ENV) or None
    if viewer_id:
        inbox.mark_viewer(viewer_id, owner=source_pane)
    try:
        # If herdr did not answer yet, keep the pane id: the viewer looks again later.
        source = pane.source_of(herdr, source_pane) or (pane.Source(source_pane) if source_pane else None)
        BlueprintApp(source=source).run()
    finally:
        if viewer_id:
            inbox.clear_viewer(viewer_id, owner=source_pane)
    return 0


if __name__ == "__main__":
    sys.exit(main())
