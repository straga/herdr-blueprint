"""An agent asks its own viewer to close while it shows a page of the agent's; the viewer decides.

A program that keeps a page in an agent's viewer (a list that can become empty) has to take the
page off the screen when there is nothing left to show, and must not close the viewer while the
person is looking at something else there. What the viewer shows can change between any look from
outside and any action from outside, so `close --if-showing <path>` only asks: the viewer takes the
request from its inbox in turn with every send, and closes when it shows that file at that moment,
the file is gone, and the person is not working in it. A send that came before the request is shown
first; the request then finds another file on screen and does nothing.

Viewers start the way `open` starts them: `herdr-blueprint` run in the viewer's pane, with the
neighbour in `BLUEPRINT_SOURCE_PANE`; the screen is the real app, given what the command gives it.
"""

import asyncio
import io
import subprocess

from herdr_blueprint import cli
from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.sources import inbox

REAL_APP = BlueprintApp


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")


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


def closed(app, herdr):
    """The viewer closed: by its own exit, or by a pane close an agent's command ran for it."""
    return app.return_code is not None or bool(pane_closes(herdr))


def pane_closes(herdr):
    return [c for c in herdr.calls if c[1:4] == ["plugin", "pane", "close"]]


def run_viewer(monkeypatch, steps):
    """A viewer beside `w1:pA` in `w1:pVA`; `steps(pilot, app, herdr)` drives it -> what it returned.
    `herdr` records every herdr command the agent's commands ran."""
    monkeypatch.setattr("herdr_blueprint.sources.pane.pane_exists", lambda herdr, pane_id: True)
    herdr = Recorder()
    monkeypatch.setattr("herdr_blueprint.cli.subprocess.run", herdr)
    out = {}

    async def look(make_screen):
        app = make_screen()
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause(0.3)
            out["result"] = await steps(pilot, app, herdr)

    beside(monkeypatch, "w1:pA", "w1:pVA", lambda make: asyncio.run(look(make)))
    return out["result"]


def ask_close(monkeypatch, pane_id, path):
    as_head(monkeypatch, pane_id)
    try:
        return cli.main(["close", "--if-showing", str(path)])
    except SystemExit as refused:  # a command herdr-blueprint does not have
        return f"refused: {refused.code}"


async def show(monkeypatch, pilot, app, path):
    as_head(monkeypatch, "w1:pA")
    assert cli.main(["show", str(path)]) == 0
    assert await settle(pilot, lambda: app.current is not None and app.current.title == path.name)


def page_and_report(tmp_path):
    page, other = tmp_path / "QUESTIONS.md", tmp_path / "report.md"
    page.write_text("# Questions", encoding="utf-8")
    other.write_text("# Report", encoding="utf-8")
    return page.resolve(), other.resolve()


# --- the race: the screen changes between a look from outside and the close ----------

def test_a_file_sent_just_before_the_request_is_not_closed(monkeypatch, tmp_path):
    """The agent's page is on screen and gone; another file is sent and, before the viewer has
    drawn it, the close is asked. The viewer must end on the other file, open."""
    page, other = page_and_report(tmp_path)

    async def steps(pilot, app, herdr):
        await show(monkeypatch, pilot, app, page)
        page.unlink()
        as_head(monkeypatch, "w1:pA")
        assert cli.main(["show", str(other)]) == 0
        ask_close(monkeypatch, "w1:pA", page)
        await settle(pilot, lambda: closed(app, herdr) or app.current.title == "report.md")
        await pilot.pause(0.5)
        return closed(app, herdr), app.current.title

    assert run_viewer(monkeypatch, steps) == (False, "report.md")


def test_text_sent_just_before_the_request_is_not_closed(monkeypatch, tmp_path):
    page, _other = page_and_report(tmp_path)

    async def steps(pilot, app, herdr):
        await show(monkeypatch, pilot, app, page)
        page.unlink()
        as_head(monkeypatch, "w1:pA")
        monkeypatch.setattr("sys.stdin", io.StringIO("# Note"))
        assert cli.main(["send", "--title", "note"]) == 0
        ask_close(monkeypatch, "w1:pA", page)
        await settle(pilot, lambda: closed(app, herdr) or app.current.title == "note")
        await pilot.pause(0.5)
        return closed(app, herdr), app.current.title

    assert run_viewer(monkeypatch, steps) == (False, "note")


# --- the viewer's own decision -----------------------------------------------------

def test_the_viewer_on_the_gone_page_closes(monkeypatch, tmp_path):
    page, _other = page_and_report(tmp_path)

    async def steps(pilot, app, herdr):
        await show(monkeypatch, pilot, app, page)
        page.unlink()
        code = ask_close(monkeypatch, "w1:pA", page)
        return code, await settle(pilot, lambda: closed(app, herdr))

    assert run_viewer(monkeypatch, steps) == (0, True)


def test_a_page_that_is_back_is_redrawn_not_closed(monkeypatch, tmp_path):
    """A question came back before the request was taken: the page is redrawn as it is now."""
    page, _other = page_and_report(tmp_path)

    async def steps(pilot, app, herdr):
        await show(monkeypatch, pilot, app, page)
        page.write_text("# Questions\n\nA new one", encoding="utf-8")
        ask_close(monkeypatch, "w1:pA", page)
        redrawn = await settle(pilot, lambda: "A new one" in app._shown_text)
        await pilot.pause(0.3)
        return closed(app, herdr), redrawn

    assert run_viewer(monkeypatch, steps) == (False, True)


def test_a_viewer_on_another_file_is_not_closed(monkeypatch, tmp_path):
    page, other = page_and_report(tmp_path)

    async def steps(pilot, app, herdr):
        await show(monkeypatch, pilot, app, other)
        page.unlink()
        ask_close(monkeypatch, "w1:pA", page)
        await pilot.pause(0.5)
        return closed(app, herdr), app.current.title

    assert run_viewer(monkeypatch, steps) == (False, "report.md")


def test_another_heads_request_does_not_reach_this_viewer(monkeypatch, tmp_path):
    page, _other = page_and_report(tmp_path)

    async def steps(pilot, app, herdr):
        await show(monkeypatch, pilot, app, page)
        page.unlink()
        code = ask_close(monkeypatch, "w1:pB", page)
        await pilot.pause(0.5)
        return code, closed(app, herdr)

    assert run_viewer(monkeypatch, steps) == (1, False)


def test_its_own_page_shown_once_gone_closes_the_viewer(monkeypatch, tmp_path):
    """The page was there when the agent sent it and gone when the viewer came to read it."""
    page, _other = page_and_report(tmp_path)

    async def steps(pilot, app, herdr):
        await show(monkeypatch, pilot, app, page)
        page.unlink()
        inbox.send({"kind": "file", "path": str(page), "draw": False, "sent_by": "agent"}, owner="w1:pA")
        return await settle(pilot, lambda: closed(app, herdr))

    assert run_viewer(monkeypatch, steps) is True


def test_outside_herdr_nothing_is_asked(monkeypatch, tmp_path):
    assert ask_close(monkeypatch, None, tmp_path / "QUESTIONS.md") == 1


def test_without_a_viewer_of_ones_own_nothing_is_asked(monkeypatch, tmp_path):
    monkeypatch.setattr("herdr_blueprint.sources.pane.pane_exists", lambda herdr, pane_id: True)
    assert ask_close(monkeypatch, "w1:pA", tmp_path / "QUESTIONS.md") == 1
