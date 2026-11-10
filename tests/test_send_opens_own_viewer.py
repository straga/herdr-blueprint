"""A head that sends without a viewer of its own gets one beside it.

An agent in a herdr pane runs `show` or `send` and often never runs `open` first. Its send then
went to the workspace inbox, which the person looking at that head never sees. Now the send opens
the head's own viewer to the right of its pane, without taking focus, and lands in that viewer's
inbox. A head that already has its own viewer, and a send from outside herdr, behave as before.
If the viewer cannot be opened, the send still goes out, to the workspace inbox.
"""

import io
import subprocess

from herdr_blueprint import cli
from herdr_blueprint.sources import inbox


class Recorder:
    def __init__(self, returncode=0, fail=None):
        self.calls = []
        self.returncode = returncode
        self.fail = fail

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self.fail:
            raise self.fail
        return subprocess.CompletedProcess(cmd, self.returncode)


def opens(calls):
    return [c for c in calls if c[1:4] == ["plugin", "pane", "open"]]


def send(monkeypatch, pane_id, title, run):
    if pane_id:
        monkeypatch.setenv("HERDR_PANE_ID", pane_id)
    monkeypatch.setattr("herdr_blueprint.sources.pane.pane_exists", lambda herdr, p: True)
    monkeypatch.setattr("herdr_blueprint.cli.subprocess.run", run)
    monkeypatch.setattr("sys.stdin", io.StringIO("# " + title))
    assert cli.main(["send", "--title", title]) == 0


def waiting(owner=None):
    return list(inbox.inbox_dir(owner=owner).glob("*.json"))


def test_a_head_without_its_own_viewer_opens_one_beside_it_and_the_send_lands_there(monkeypatch):
    run = Recorder()
    send(monkeypatch, "w1:pA", "report", run)

    [cmd] = opens(run.calls)
    assert cmd[cmd.index("--target-pane") + 1] == "w1:pA"
    assert "--focus" not in cmd  # the person keeps looking where they were
    assert len(waiting(owner="w1:pA")) == 1
    assert waiting() == []


def test_a_head_with_its_own_viewer_opens_nothing(monkeypatch):
    inbox.mark_viewer("w1:pVA", owner="w1:pA")
    run = Recorder()
    send(monkeypatch, "w1:pA", "report", run)

    assert opens(run.calls) == []
    assert len(waiting(owner="w1:pA")) == 1


def test_a_send_outside_herdr_opens_nothing_and_goes_to_the_workspace(monkeypatch):
    run = Recorder()
    send(monkeypatch, None, "outside", run)

    assert opens(run.calls) == []
    assert len(waiting()) == 1


def test_when_the_viewer_cannot_be_opened_the_send_still_goes_to_the_workspace(monkeypatch):
    run = Recorder(returncode=1)
    send(monkeypatch, "w1:pA", "report", run)

    assert len(opens(run.calls)) == 1
    assert waiting(owner="w1:pA") == []
    assert len(waiting()) == 1


def test_when_herdr_is_missing_the_send_still_goes_to_the_workspace(monkeypatch):
    run = Recorder(fail=FileNotFoundError("herdr"))
    send(monkeypatch, "w1:pA", "report", run)

    assert waiting(owner="w1:pA") == []
    assert len(waiting()) == 1


class SlowHerdr(Recorder):
    """herdr that takes a moment to open a pane and answers with its id, as the real one does."""

    def __init__(self):
        super().__init__()
        self.count = 0

    def __call__(self, cmd, **kwargs):
        import json
        import time

        self.calls.append(cmd)
        if cmd[1:4] == ["plugin", "pane", "open"]:
            time.sleep(0.3)
            self.count += 1
            out = {"result": {"plugin_pane": {"pane": {"pane_id": f"w1:pV{self.count}"}}}}
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(out), stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")


def test_two_sends_at_once_open_one_viewer(monkeypatch):
    import threading

    monkeypatch.setenv("HERDR_PANE_ID", "w1:pA")
    monkeypatch.setattr("herdr_blueprint.sources.pane.pane_exists", lambda herdr, p: True)
    run = SlowHerdr()
    monkeypatch.setattr("herdr_blueprint.cli.subprocess.run", run)

    def one(title):
        cli.sender_opening_own_viewer("herdr", "w1:pA", cli.own_inbox("herdr", "w1:pA"))

    threads = [threading.Thread(target=one, args=(t,)) for t in ("first", "second")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(opens(run.calls)) == 1


def test_a_second_send_right_after_the_first_opens_nothing_new(monkeypatch):
    run = SlowHerdr()
    send(monkeypatch, "w1:pA", "first", run)
    send(monkeypatch, "w1:pA", "second", run)

    assert len(opens(run.calls)) == 1
    assert len(waiting(owner="w1:pA")) == 2


def test_open_and_a_send_right_after_it_open_one_viewer(monkeypatch):
    run = SlowHerdr()
    monkeypatch.setattr("herdr_blueprint.sources.pane.pane_exists", lambda herdr, p: True)
    assert cli.open_viewer("herdr", "w1:pA", run=run) == 0
    send(monkeypatch, "w1:pA", "right after open", run)

    assert len(opens(run.calls)) == 1
    assert len(waiting(owner="w1:pA")) == 1
