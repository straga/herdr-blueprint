"""Blueprint reports the mouse in cells, never in pixels.

Textual turns on SGR pixel mouse reporting (`?1016h`) once the terminal answers its in-band resize
query. Inside herdr the mode reaches the client's own terminal, and a client that maps pixels to
cells wrongly (a Retina screen seen 06.11) puts every click somewhere else: dragging Blueprint's
scrollbar at the right edge selected text in the left half of the window. In cells the mouse lands
where it points.
"""

from textual.messages import InBandWindowResize

from herdr_blueprint.app import BlueprintApp
from herdr_blueprint.config import Config

PIXELS = "\x1b[?1016h"


def driver_of_the_viewer():
    """The driver Blueprint runs on, its output kept. -> `(driver, written)`."""
    app = BlueprintApp(settings=Config(theme="rose-pine"))
    driver = app.driver_class(app, mouse=True)
    written = []
    driver.write = written.append
    return driver, written


async def test_the_terminal_answering_the_resize_query_does_not_turn_pixels_on():
    driver, written = driver_of_the_viewer()

    driver.process_message(InBandWindowResize(True, True))

    assert PIXELS not in "".join(written)


async def test_a_terminal_that_supports_but_has_not_enabled_resize_gets_no_pixels_either():
    driver, written = driver_of_the_viewer()

    driver.process_message(InBandWindowResize(True, False))

    assert PIXELS not in "".join(written)


async def test_the_mouse_is_still_reported_in_cells():
    """PAIRED: without pixels the mouse is not off: any-event tracking with SGR cells stays."""
    driver, written = driver_of_the_viewer()

    driver._enable_mouse_support()

    out = "".join(written)
    assert "\x1b[?1003h" in out and "\x1b[?1006h" in out
