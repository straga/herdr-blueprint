"""An agent closes its own viewer, but only while that viewer shows the page the agent names.

A program that keeps a page in an agent's viewer (a list that can become empty) has to take the
page off the screen when there is nothing left to show. It must not close the viewer while the
person is looking at something else there: the viewer remembers which file it shows, and
`close --if-showing <path>` closes the agent's own viewer only when it shows that file.

Viewers start the way `open` starts them: `herdr-blueprint` run in the viewer's pane, with the
neighbour in `BLUEPRINT_SOURCE_PANE`; the screen is the real app, given what the command gives it.
"""

import asyncio
import io
import subprocess

from herdr_blueprint import cli
from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config

REAL_APP = BlueprintApp


class Recorder:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, "", "")


def closes(calls):
    return [c[-1] for c in calls if c[1:4] == ["plugin", "pane", "close"]]


def panes_alive(monkeypatch, alive=lambda pane_id: True):
    monkeypatch.setattr("herdr_blueprint.sources.pane.pane_exists", lambda herdr, pane_id: alive(pane_id))


def as_head(monkeypatch, pane_id):
    if pane_id:
        monkeypatch.setenv("HERDR_PANE_ID", pane_id)
    else:
        monkeypatch.delenv("HERDR_PANE_ID", raising=False)


def beside(monkeypatch, head, viewer_pane, then):
    """Run the viewer in `viewer_pane` next to `head`; `then(make_screen)` runs while it is up,
    where `make_screen()` builds the real app with exactly what the command handed it."""
    seen = {}

    class Up:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run(self):
            then(lambda: REAL_APP(**{**seen, "ask_on_open": False, "settings": Config(theme="rose-pine"),
                                     "refresher": lambda pane_id: seen.get("source")}))

    monkeypatch.setattr("herdr_blueprint.app.BlueprintApp", Up)
    monkeypatch.setattr("herdr_blueprint.sources.pane.source_of", lambda herdr, pane_id: None)
    monkeypatch.setenv(cli.SOURCE_ENV, head)
    monkeypatch.setenv("HERDR_PANE_ID", viewer_pane)
    assert cli.main([]) == 0


async def settle(pilot, done, seconds=2.0):
    for _ in range(int(seconds / 0.05)):
        if done():
            return True
        await pilot.pause(0.05)
    return False


def close_as(monkeypatch, pane_id, path, run):
    as_head(monkeypatch, pane_id)
    monkeypatch.setattr("herdr_blueprint.cli.subprocess.run", run)
    try:
        return cli.main(["close", "--if-showing", str(path)])
    except SystemExit as refused:  # a command herdr-blueprint does not have
        return f"refused: {refused.code}"


def run_viewer(monkeypatch, tmp_path, steps):
    """A viewer beside `w1:pA` in `w1:pVA`; `steps(pilot, app)` drives it. -> what steps returned."""
    panes_alive(monkeypatch)
    out = {}

    async def look(make_screen):
        app = make_screen()
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause(0.3)
            out["result"] = await steps(pilot, app)

    beside(monkeypatch, "w1:pA", "w1:pVA", lambda make: asyncio.run(look(make)))
    return out["result"]


def test_the_viewer_showing_the_page_is_closed(monkeypatch, tmp_path):
    page = tmp_path / "QUESTIONS.md"
    page.write_text("# Questions", encoding="utf-8")

    async def steps(pilot, app):
        as_head(monkeypatch, "w1:pA")
        assert cli.main(["show", str(page)]) == 0
        assert await settle(pilot, lambda: app.current is not None and app.current.title == "QUESTIONS.md")
        run = Recorder()
        return close_as(monkeypatch, "w1:pA", page, run), closes(run.calls)

    assert run_viewer(monkeypatch, tmp_path, steps) == (0, ["w1:pVA"])


def test_a_viewer_showing_another_page_is_not_closed(monkeypatch, tmp_path):
    page, other = tmp_path / "QUESTIONS.md", tmp_path / "report.md"
    page.write_text("# Questions", encoding="utf-8")
    other.write_text("# Report", encoding="utf-8")

    async def steps(pilot, app):
        as_head(monkeypatch, "w1:pA")
        assert cli.main(["show", str(page)]) == 0
        assert await settle(pilot, lambda: app.current is not None and app.current.title == "QUESTIONS.md")
        assert cli.main(["show", str(other)]) == 0
        assert await settle(pilot, lambda: app.current.title == "report.md")
        run = Recorder()
        return close_as(monkeypatch, "w1:pA", page, run), closes(run.calls)

    code, closed = run_viewer(monkeypatch, tmp_path, steps)
    assert closed == []
    assert code == 1


def test_a_viewer_showing_sent_text_is_not_closed(monkeypatch, tmp_path):
    page = tmp_path / "QUESTIONS.md"
    page.write_text("# Questions", encoding="utf-8")

    async def steps(pilot, app):
        as_head(monkeypatch, "w1:pA")
        assert cli.main(["show", str(page)]) == 0
        assert await settle(pilot, lambda: app.current is not None and app.current.title == "QUESTIONS.md")
        monkeypatch.setattr("sys.stdin", io.StringIO("# Note"))
        assert cli.main(["send", "--title", "note"]) == 0
        assert await settle(pilot, lambda: app.current.title == "note")
        run = Recorder()
        return close_as(monkeypatch, "w1:pA", page, run), closes(run.calls)

    assert run_viewer(monkeypatch, tmp_path, steps) == (1, [])


def test_another_heads_viewer_is_not_closed(monkeypatch, tmp_path):
    page = tmp_path / "QUESTIONS.md"
    page.write_text("# Questions", encoding="utf-8")

    async def steps(pilot, app):
        as_head(monkeypatch, "w1:pA")
        assert cli.main(["show", str(page)]) == 0
        assert await settle(pilot, lambda: app.current is not None and app.current.title == "QUESTIONS.md")
        run = Recorder()
        return close_as(monkeypatch, "w1:pB", page, run), closes(run.calls)

    assert run_viewer(monkeypatch, tmp_path, steps) == (1, [])


def test_outside_herdr_nothing_is_closed(monkeypatch, tmp_path):
    run = Recorder()
    assert close_as(monkeypatch, None, tmp_path / "QUESTIONS.md", run) == 1
    assert run.calls == []


def test_a_close_herdr_refuses_leaves_the_viewer_recorded(monkeypatch, tmp_path):
    from herdr_blueprint.sources import inbox

    page = (tmp_path / "QUESTIONS.md").resolve()
    panes_alive(monkeypatch)
    inbox.mark_viewer("w1:pVA", owner="w1:pA")
    inbox.mark_shown("w1:pVA", page, owner="w1:pA")
    run = Recorder(returncode=1)
    assert close_as(monkeypatch, "w1:pA", page, run) == 1
    assert closes(run.calls) == ["w1:pVA"]
    assert inbox.viewer_panes(owner="w1:pA") == ["w1:pVA"]
