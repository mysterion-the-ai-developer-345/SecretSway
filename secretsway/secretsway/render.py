"""Composing and drawing the panel.

The split here is deliberate.  `Layout` is pure Python -- it turns a query, a
result list and a config into positioned text runs, and knows nothing about
Cairo or GTK.  That is the part worth testing, and it is all of the tricky
logic: column alignment, ellipsis, where the highlight falls, how tall the strip
needs to be.  `draw()` is a thin shell that paints what Layout produced.

The frame is drawn as Cairo rectangles rather than as box-drawing glyphs.  A
1px hairline is pixel-exact and does not care whether the user's font has
U+2551 in it, nor whether the output width happens to be an exact multiple of
the cell width -- both of which silently ruin a literal TUI look.

    ╭──────────────────────────────────╮
    │ you@host ~]$ > fire_              │
    ├──────────────────────────────────┤
    │  1  Firefox             /usr/bin │
    │  2  Firefox Developer   flatpak │
    ├──────────────────────────────────┤
    │ 2/187   esc quit                │
    ╰──────────────────────────────────╯
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Colour roles, resolved to hex by the caller.  Keeping them symbolic is what
# lets the layout be tested without a palette.
TEXT = "text"
DIM = "dim"
CURSOR = "cursor"
SEL_BG = "sel_bg"
SEL_FG = "sel_fg"

ELLIPSIS = "…"        # …
# Plain ASCII on purpose.  The panel is a character grid, so anything a font
# lacks -- or renders at a different width than the cell -- shifts every
# column after it.  U+25B8 and U+258C were both wrong on common monospace
# faces; ">" and "_" cannot be.
PROMPT_MARK = ">"
CURSOR_BLOCK = "_"
HINT_QUIT = "esc"

# Only "esc quit".  The launch and action hints were prefixed with U+21B5 (↵),
# which many monospace fonts either lack or render at a different width -- and
# a glyph of the wrong width puts the whole character grid out by a column.
# The keys are documented in the README instead, where a missing glyph costs
# nothing.
HINTS = f"{HINT_QUIT} quit"


@dataclass
class Segment:
    """A run of text sharing one foreground, and optionally a background."""

    text: str
    fg: str = TEXT
    bg: str | None = None
    hits: frozenset = field(default_factory=frozenset)


@dataclass
class Row:
    """One result line, already reduced to what the renderer needs."""

    name: str
    right: str = ""
    hits: frozenset = field(default_factory=frozenset)
    index: int = 0
    selected: bool = False
    is_command: bool = False


@dataclass
class Metrics:
    # cell_w is a *float* on purpose.  Rounding the font's advance to a whole
    # pixel makes the assumed grid drift away from where the glyphs actually
    # land, by a fraction of a pixel per character -- so the further right a
    # string starts, the further off it ends.  That is invisible in a text
    # preview and obvious on screen.
    cell_w: float = 8.0
    cell_h: int = 18
    ascent: int = 14
    # The exact size the grid was measured at.  Drawing at any *other* size
    # rescales the advance, so every column lands somewhere other than where
    # the layout put it -- the renderer must use this, not a size derived from
    # the cell height.
    size: float = 14.0
    family: str = ""
    monospace: bool = True


def ellipsize(text: str, width: int) -> str:
    """Trim to `width` cells, marking the cut with an ellipsis."""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return ELLIPSIS
    return text[: width - 1] + ELLIPSIS


def clamp(value: int, low: int, high: int) -> int:
    """Constrain to [low, high].  An inverted range collapses to `low`."""
    if high < low:
        return low
    return max(low, min(value, high))


def move_index(current: int, delta: int, total: int, wrap: bool = False) -> int:
    """Step the selection by `delta`, clamped to the ends by default.

    With `wrap` the list cycles instead, which is the fuzzel behaviour; clamping
    is the default because a launcher that jumps from the last entry back to the
    first on a stray `Up` is disorienting.
    """
    if total <= 0:
        return 0
    if wrap:
        return (current + delta) % total
    return clamp(current + delta, 0, total - 1)


def scroll_for(scroll: int, selected: int, total: int, max_rows: int) -> int:
    """Where the window must sit for `selected` to be visible.

    Moves the window only when the selection would fall outside it, so arrowing
    within the visible range does not make the list drift.  Always clamped to
    [0, total - max_rows] so the last page is never padded with blank rows.
    """
    span = max(0, max_rows)
    ceiling = max(0, total - span)
    if span == 0 or total == 0:
        return 0
    top = clamp(scroll, 0, ceiling)
    if selected < top:
        top = selected
    elif selected >= top + span:
        top = selected - span + 1
    return clamp(top, 0, ceiling)


@dataclass
class Layout:
    """Everything needed to paint the strip, computed without drawing it."""

    rows: list = field(default_factory=list)
    selected: int = 0
    total: int = 0
    query: str = ""
    prefix: str = ""                 # e.g. "you@host ~]$"
    command: str = ""                # the run-as-command text, if offered
    last_name: str = ""              # most recently launched app, for the status line
    metrics: Metrics = field(default_factory=Metrics)
    cols: int = 80
    max_rows: int = 15
    scroll: int = 0
    pad_x: int = 2
    show_index: bool = True
    right_column: str = "dots"   # "dots" | "exec" | "off"
    cursor_on: bool = True

    GUTTER = 4                       # " 12 " -- three digits plus a space

    def visible_rows(self) -> list:
        """The slice of rows currently on screen."""
        start = clamp(self.scroll, 0, max(0, len(self.rows) - max(0, self.max_rows)))
        return self.rows[start : start + max(0, self.max_rows)]

    def selected_visible(self) -> bool:
        """True when the selected row is actually on screen."""
        if not self.rows or not 0 <= self.selected < len(self.rows):
            return False
        start = clamp(self.scroll, 0, max(0, len(self.rows) - max(0, self.max_rows)))
        return start <= self.selected < start + max(0, self.max_rows)

    @property
    def line_count(self) -> int:
        """Header + rows + status.  The frame and rules are drawn separately."""
        return 2 + len(self.visible_rows())

    @property
    def height(self) -> int:
        return self.line_count * self.metrics.cell_h

    def content_width(self) -> int:
        return max(8, self.cols - 2 - 2 * self.pad_x)

    def right_width(self, rows: list | None = None) -> int:
        """Width of the right-hand column.

        Measured across *all* rows, not just the visible ones: scoping it to the
        window would make the column resize on every scroll, which reads as
        flicker.
        """
        if self.right_column == "off":
            return 0
        source = self.rows if rows is None else rows
        longest = max((len(r.right) for r in source), default=0)
        gutter = self.GUTTER if self.show_index else 0
        budget = self.content_width() - gutter - 4
        return max(0, min(longest, budget, 28))

    def header_line(self) -> list:
        segs = []
        used = 0

        # Reserve room for the marker and the cursor before sizing the prefix,
        # so a long user@host on a narrow output cannot push them off-panel.
        reserve = len(PROMPT_MARK) + 1 + (1 if self.cursor_on else 0)
        if self.prefix:
            budget = max(0, self.content_width() - reserve)
            prefix = ellipsize(self.prefix, budget)
            if prefix:
                segs.append(Segment(prefix + " ", DIM))
                used += len(prefix) + 1

        segs.append(Segment(PROMPT_MARK + " ", CURSOR))
        used += len(PROMPT_MARK) + 1

        # The query is what the user is typing, so it gets every cell that is
        # left over -- but never so many that the cursor is pushed off-panel.
        room = self.content_width() - used - (1 if self.cursor_on else 0)
        if self.query and room > 0:
            query = ellipsize(self.query, room)
            segs.append(Segment(query, TEXT))
            used += len(query)
        if self.cursor_on and used < self.content_width():
            segs.append(Segment(CURSOR_BLOCK, CURSOR))
        return segs

    def row_line(self, row: Row, right_w: int) -> list:
        content_w = self.content_width()
        gutter = self.GUTTER if self.show_index else 0
        sel = row.selected

        segs = []
        if self.show_index:
            label = "" if row.is_command else f"{row.index:d}"
            segs.append(Segment(f"{label:>{gutter - 1}} ", SEL_FG if sel else DIM))

        name_w = max(1, content_w - gutter - (right_w + 1 if right_w else 0))
        name = ellipsize(row.name, name_w)

        segs.append(Segment(
            name,
            SEL_FG if sel else TEXT,
            SEL_BG if sel else None,
            row.hits,
        ))

        if right_w:
            right = ellipsize(row.right, right_w)
            # Pin the right column to the panel's right edge.  Deriving the
            # start from the name length instead put it one column further
            # right for any name that exactly filled its field, so the column
            # visibly jogged as you scrolled.
            target = content_w - right_w
            pad = max(1, target - (gutter + len(name)))
            segs.append(Segment(
                " " * pad + right,
                SEL_FG if sel else DIM,
                SEL_BG if sel else None,
            ))
        return segs

    def command_line(self) -> list:
        sel = self.selected == 0
        return [
            Segment(" ", SEL_FG if sel else DIM),
            Segment(
                ellipsize(f"run as command: {self.command}", self.content_width() - 1),
                SEL_FG if sel else TEXT,
                SEL_BG if sel else None,
            ),
        ]

    def status_line(self) -> list:
        shown = (self.selected + 1) if self.rows else 0
        left = f"{shown}/{self.total}"
        segs = [Segment(" ", DIM), Segment(left, DIM)]
        used = 1 + len(left)

        # "last: <app>" sits next to the position, but only if it fits --
        # otherwise the key legend, which is more useful, keeps its room.
        if self.last_name:
            note = f"last: {ellipsize(self.last_name, 24)}"
            if used + 1 + len(note) + 1 < self.content_width():
                segs.append(Segment("  " + note, DIM))
                used += 2 + len(note)

        room = self.content_width() - used
        if room >= len(HINTS) + 1:
            segs.append(Segment(" " * (room - len(HINTS)), DIM))
            segs.append(Segment(HINTS, DIM))
        elif room > 1:
            segs.append(Segment(" ", DIM))
            segs.append(Segment(ellipsize(HINTS, room - 1), DIM))
        return segs

    def lines(self) -> list:
        """Every text line of the panel, top to bottom."""
        rows = self.visible_rows()
        # Measured over every row, not the window, so the column holds still
        # while the list scrolls under it.
        right_w = self.right_width()
        out = [self.header_line()]
        for row in rows:
            out.append(
                self.command_line() if row.is_command else self.row_line(row, right_w)
            )
        out.append(self.status_line())
        return out

    def plain_lines(self) -> list:
        """`lines()` as bare strings -- what the tests assert on."""
        return ["".join(seg.text for seg in line) for line in self.lines()]


def resolve_family(chain: str) -> str:
    """Pick the first family in a comma-separated chain that actually exists.

    Cairo's toy font API takes a single family name and does not understand a
    fallback list, so the chain has to be resolved through Pango (which does
    honour it) before being handed over.  Falling back to the *first* entry is
    wrong: if that family is not installed, Cairo would silently substitute a
    proportional default and the character grid would collapse.  Ending on the
    generic `monospace` is what keeps the grid intact.
    """
    try:
        from gi.repository import Pango
        desc = Pango.FontDescription()
        desc.set_family(chain)
        resolved = desc.get_family()
        if resolved and "," not in resolved:
            return resolved
    except Exception:  # pragma: no cover - Pango missing or no fontconfig
        pass
    return chain.split(",")[0].strip() or "monospace"


def pango_context(headless: bool = True):
    """A Pango context to measure with.

    `headless=True` builds one from a 1x1 Cairo image surface, which needs
    neither a display nor an initialised Gtk -- so the font check can run on a
    machine with no compositor.  Passing False is not supported; use
    `widget.create_pango_layout(None).get_context()` from a real widget.
    """
    import gi
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import PangoCairo
    import cairo

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    context = cairo.Context(surface)
    return PangoCairo.create_context(context)


def measure(ctx, family: str, size_px: float) -> Metrics:
    """Read the exact cell size off a real Pango layout.

    Pango is used rather than Cairo's toy text API because the toy API cannot
    see a fallback chain and gives no way to check that the font it landed on
    is fixed-pitch.  Both matter here: the panel is a character grid, and a
    proportional substitute ruins it in a way that only shows up on screen.
    """
    try:
        from gi.repository import Pango
    except Exception:  # pragma: no cover
        return Metrics()

    try:
        sample = Pango.Layout.new(ctx)
        sample.set_text("0" * 100)
        desc = Pango.FontDescription()
        # A chain here is fine: Pango resolves it, unlike Cairo.
        desc.set_family(family)
        desc.set_size(int(round(size_px * Pango.SCALE)))
        sample.set_font_description(desc)

        _, logical = sample.get_pixel_extents()
        cell_w = logical.width / 100.0
        cell_h = int(logical.height) or int(round(size_px * 1.35))

        # A proportional font would give "iiii" and "WWWW" different widths.
        narrow = Pango.Layout.new(ctx)
        narrow.set_text("i" * 50)
        narrow.set_font_description(desc)
        wide = Pango.Layout.new(ctx)
        wide.set_text("W" * 50)
        wide.set_font_description(desc)
        monospace = abs(narrow.get_pixel_extents()[1].width
                        - wide.get_pixel_extents()[1].width) < 0.5

        # Baseline from the same layout, so rows are not vertically guessed at.
        ascent = int(round(sample.get_baseline() / Pango.SCALE)) or int(
            round(cell_h * 0.78))
        return Metrics(
            cell_w=cell_w or 8.0,
            cell_h=max(1, cell_h),
            ascent=ascent,
            size=float(size_px),
            family=family,
            monospace=monospace,
        )
    except Exception:  # pragma: no cover - only on a broken font stack
        return Metrics()


def font_report(ctx, family: str, size_px: float) -> str:
    """One line describing the font the panel will actually use."""
    m = measure(ctx, family, size_px)
    if not m.family:
        return f"font: unmeasurable (family {family!r})"
    verdict = "monospace" if m.monospace else "NOT MONOSPACED -- grid will drift"
    return (f"font: {m.family!r} cell={m.cell_w:.3f}px "
            f"(rounded to {round(m.cell_w)}) {verdict}")


def draw(cr, layout: Layout, colors: dict, family: str, border_w: int = 1) -> None:
    """Paint the panel.  The context is assumed clipped to the surface."""
    from secretsway.config import rgb

    m = layout.metrics
    width = layout.cols * m.cell_w
    height = layout.height


    def fill(x, y, w, h, colour):
        cr.set_source_rgb(*rgb(colors[colour]))
        cr.rectangle(x, y, w, h)
        cr.fill()

    fill(0, 0, width, height, "panel")

    # Rules: under the prompt, and above the status line.  Both are at fixed
    # offsets -- the status line always sits on the last row.
    fill(border_w, m.cell_h, width - 2 * border_w, max(1, border_w), "border")
    status_top = height - m.cell_h
    fill(border_w, status_top, width - 2 * border_w, max(1, border_w), "border")

    # Outline last, so it sits over the rule ends.
    cr.set_source_rgb(*rgb(colors["border"]))
    cr.set_line_width(border_w)
    cr.rectangle(border_w / 2, border_w / 2, width - border_w, height - border_w)
    cr.stroke()

    # A single resolved family, never the comma-separated chain: Cairo's toy
    # API would fail to match it and silently substitute a proportional font.
    try:
        cr.select_font_face(m.family or family, 0, 0)
        # The measured size, *not* something derived from the cell height.  A
        # different size here rescales the glyph advance, so the rendered text
        # no longer fills the cells the layout was computed against -- which
        # pushes the right-hand column further right the longer the app name
        # is.  That was the drift.
        cr.set_font_size(m.size)
    except Exception:  # pragma: no cover
        pass

    y = m.ascent
    for line in layout.lines():
        # Pixels, from a float cell width, so the grid does not drift.
        x = border_w + layout.pad_x * m.cell_w
        for seg in line:
            if seg.bg:
                fill(x, y - m.ascent, len(seg.text) * m.cell_w, m.cell_h, seg.bg)
            if seg.hits:
                # Draw per character so the matched cells can take the accent
                # colour.  Monospace makes the cell arithmetic exact.
                for i, ch in enumerate(seg.text):
                    if ch.strip():
                        cr.set_source_rgb(*rgb(colors[CURSOR if i in seg.hits else seg.fg]))
                        cr.move_to(x, y)
                        cr.show_text(ch)
                    x += m.cell_w
            else:
                cr.set_source_rgb(*rgb(colors[seg.fg]))
                cr.move_to(x, y)
                cr.show_text(seg.text)
                x += len(seg.text) * m.cell_w
        y += m.cell_h
