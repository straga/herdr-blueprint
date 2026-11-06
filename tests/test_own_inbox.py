"""Each head reads and writes the inbox of its own viewer.

Several agents share one herdr workspace. A viewer opened next to agent A shows what A sends and
nothing of B's; an agent without a viewer of its own, and a send from outside herdr, behave as
before: the workspace's inbox, read by a viewer that has no neighbour.

Viewers here start the way `open` starts them: `herdr-blueprint` run in the viewer's pane, with
the neighbour in `BLUEPRINT_SOURCE_PANE`. Only the screen is swapped, for a test app given the
same neighbour the command worked out.
"""

import asyncio
import io

from herdr_blueprint import cli
from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config
from herdr_blueprint.document import DocumentView

REAL_APP = BlueprintApp


def beside(monkeypatch, head, viewer_pane, then):
    """Run the viewer in `viewer_pane` next to the agent pane `head` (`None`: opened with no
    neighbour), and call `then(source)` while it is up."""
    seen = {}

    class Up:
        def __init__(self, source=None, **kwargs):
            seen["source"] = source

        def run(self):
            then(seen["source"])

    monkeypatch.setattr("herdr_blueprint.app.BlueprintApp", Up)
    monkeypatch.setattr("herdr_blueprint.sources.pane.source_of", lambda herdr, pane_id: None)
    if head:
        monkeypatch.setenv(cli.SOURCE_ENV, head)
    else:
        monkeypatch.delenv(cli.SOURCE_ENV, raising=False)
    monkeypatch.setenv("HERDR_PANE_ID", viewer_pane)
    assert cli.main([]) == 0


def screen(source) -> BlueprintApp:
    """The viewer's screen, with the neighbour the command gave it."""
    return REAL_APP(ask_on_open=False, settings=Config(theme="rose-pine"), source=source,
                    refresher=lambda pane_id: source)


def as_head(monkeypatch, pane_id):
    """The commands that follow run in the agent pane `pane_id`; `None` — outside herdr."""
    if pane_id:
        monkeypatch.setenv("HERDR_PANE_ID", pane_id)
    else:
        monkeypatch.delenv("HERDR_PANE_ID", raising=False)


def send_as(monkeypatch, pane_id, title, body="# Text"):
    as_head(monkeypatch, pane_id)
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    assert cli.main(["send", "--title", title]) == 0


def status_of(monkeypatch, pane_id) -> int:
    as_head(monkeypatch, pane_id)
    return cli.main(["status"])


async def settle(pilot, app, done, seconds=2.0):
    """Waits for `done()`; -> whether it came."""
    for _ in range(int(seconds / 0.05)):
        if done():
            return True
        await pilot.pause(0.05)
    return False


def shows(app, title):
    return lambda: app.current is not None and app.current.title == title


def panes_alive(monkeypatch, alive=lambda pane_id: True):
    monkeypatch.setattr("herdr_blueprint.sources.pane.pane_exists", lambda herdr, pane_id: alive(pane_id))


# --- two heads in one workspace ------------------------------------------------------

def test_the_viewer_beside_a_shows_a_and_not_b(monkeypatch):
    panes_alive(monkeypatch)
    seen = {}

    async def look(source):
        app = screen(source)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause(0.3)
            send_as(monkeypatch, "w1:pB", "from B")
            seen["B beside A"] = await settle(pilot, app, shows(app, "from B"), seconds=1.0)
            send_as(monkeypatch, "w1:pA", "from A")
            seen["A beside A"] = await settle(pilot, app, shows(app, "from A"))

    beside(monkeypatch, "w1:pB", "w1:pVB",
           lambda _b: beside(monkeypatch, "w1:pA", "w1:pVA", lambda a: asyncio.run(look(a))))
    assert seen == {"B beside A": False, "A beside A": True}


def test_each_head_sees_its_own_and_nothing_is_lost(monkeypatch):
    """A message is claimed by one viewer: B's send must wait for B's viewer, not be taken by A's."""
    panes_alive(monkeypatch)
    seen = {}

    async def look(source_a, source_b):
        beside_a = screen(source_a)
        async with beside_a.run_test(size=(80, 30)) as pilot:
            await pilot.pause(0.3)
            send_as(monkeypatch, "w1:pB", "from B")
            send_as(monkeypatch, "w1:pA", "from A")
            seen["A"] = await settle(pilot, beside_a, shows(beside_a, "from A"))
        beside_b = screen(source_b)
        async with beside_b.run_test(size=(80, 30)) as pilot:
            seen["B"] = await settle(pilot, beside_b, shows(beside_b, "from B"))

    beside(monkeypatch, "w1:pB", "w1:pVB",
           lambda b: beside(monkeypatch, "w1:pA", "w1:pVA", lambda a: asyncio.run(look(a, b))))
    assert seen == {"A": True, "B": True}


def test_a_refresh_from_b_does_not_redraw_the_viewer_beside_a(monkeypatch, tmp_path):
    panes_alive(monkeypatch)
    doc = tmp_path / "board.md"
    doc.write_text("# Old", encoding="utf-8")
    seen = {}

    def on_screen(app):
        return bool(app.query(DocumentView)) and "# New" in app.query_one(DocumentView).source

    async def look(source):
        app = screen(source)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause(0.3)
            as_head(monkeypatch, "w1:pA")
            assert cli.main(["show", str(doc)]) == 0
            assert await settle(pilot, app, shows(app, "board.md"))
            doc.write_text("# New", encoding="utf-8")
            as_head(monkeypatch, "w1:pB")
            assert cli.main(["refresh"]) == 0
            seen["B's refresh"] = await settle(pilot, app, lambda: on_screen(app), seconds=1.0)
            as_head(monkeypatch, "w1:pA")
            assert cli.main(["refresh"]) == 0
            seen["A's refresh"] = await settle(pilot, app, lambda: on_screen(app))

    beside(monkeypatch, "w1:pB", "w1:pVB",
           lambda _b: beside(monkeypatch, "w1:pA", "w1:pVA", lambda a: asyncio.run(look(a))))
    assert seen == {"B's refresh": False, "A's refresh": True}


def test_status_tells_each_head_of_its_own_viewer(monkeypatch):
    panes_alive(monkeypatch)
    seen = {}

    def look(_source):
        seen["A"] = status_of(monkeypatch, "w1:pA")
        seen["B"] = status_of(monkeypatch, "w1:pB")

    beside(monkeypatch, "w1:pA", "w1:pVA", look)
    assert seen == {"A": 0, "B": 1}


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kwargs):
        import subprocess

        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)


def test_open_from_b_opens_b_its_own_viewer_and_does_not_focus_a_s(monkeypatch):
    runs = {}

    def look(_source):
        for head in ("w1:pA", "w1:pB"):
            runs[head] = Recorder()
            cli.open_viewer("herdr", head, run=runs[head], exists=lambda herdr, pane_id: True)

    beside(monkeypatch, "w1:pA", "w1:pVA", look)
    assert runs["w1:pA"].calls == [["herdr", "plugin", "pane", "focus", "w1:pVA"]]
    assert runs["w1:pB"].calls == [cli.open_command("herdr", "w1:pB")]


# --- one head, and outside herdr, as before ------------------------------------------

def test_a_head_without_a_viewer_of_its_own_reaches_a_viewer_with_no_neighbour(monkeypatch):
    panes_alive(monkeypatch)
    seen = {}

    async def look(source):
        app = screen(source)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause(0.3)
            send_as(monkeypatch, "w1:pA", "alone")
            seen["shown"] = await settle(pilot, app, shows(app, "alone"))
            seen["status"] = status_of(monkeypatch, "w1:pA")

    beside(monkeypatch, None, "w1:pV", lambda source: asyncio.run(look(source)))
    assert seen == {"shown": True, "status": 0}


def test_a_send_outside_herdr_reaches_a_viewer_with_no_neighbour(monkeypatch):
    panes_alive(monkeypatch)
    seen = {}

    async def look(source):
        app = screen(source)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause(0.3)
            send_as(monkeypatch, None, "outside")
            seen["shown"] = await settle(pilot, app, shows(app, "outside"))

    beside(monkeypatch, None, "w1:pV", lambda source: asyncio.run(look(source)))
    assert seen == {"shown": True}


def test_a_head_whose_viewer_closed_without_cleanup_sends_as_a_head_without_one(monkeypatch):
    """A's viewer pane is gone but its record stays: A's sends must not go to a folder no one reads."""
    panes_alive(monkeypatch, lambda pane_id: pane_id != "w1:pVA")
    seen = {}

    async def look(source):
        app = screen(source)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause(0.3)
            send_as(monkeypatch, "w1:pA", "after close")
            seen["shown"] = await settle(pilot, app, shows(app, "after close"))
            seen["status"] = status_of(monkeypatch, "w1:pA")

    beside(monkeypatch, None, "w1:pV",
           lambda plain: beside(monkeypatch, "w1:pA", "w1:pVA", lambda _a: asyncio.run(look(plain))))
    assert seen == {"shown": True, "status": 0}


def test_open_from_outside_herdr_focuses_the_viewer_with_no_neighbour(monkeypatch):
    run = Recorder()
    beside(monkeypatch, None, "w1:pV",
           lambda _source: cli.open_viewer("herdr", None, run=run, exists=lambda herdr, pane_id: True))
    assert run.calls == [["herdr", "plugin", "pane", "focus", "w1:pV"]]
