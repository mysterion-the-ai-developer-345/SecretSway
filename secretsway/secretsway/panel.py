"""Panel composition, with no GTK anywhere in it.

Split out of the window so that the same code path that draws the strip can be
run headlessly by `secretsway --preview`.  Rendering bugs like a right-hand column that
drifts with the length of the app name are then something you can see and check
in a terminal, instead of something that only shows up once a compositor is
involved.
"""

from __future__ import annotations

import os
import socket

from secretsway import render
from secretsway.match import rank
from secretsway.usage import Usage, dots_for


def default_prefix() -> str:
    """The real user/host prompt, so --preview matches what you will see."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "user"
    try:
        host = socket.gethostname().split(".")[0]
    except Exception:
        host = "sway"
    return f"{user}@{host} ~]$"


def browse_matches(query: str, apps, usage: Usage) -> list:
    """Result tuples for `query`.

    A typed query gets pure fuzzy ranking.  An empty one gets usage order, with
    the most recent launch pinned to the front -- reordering by history while
    someone is mid-word would fight what they are asking for.
    """
    if query.strip():
        return rank(query, apps)
    ordered = usage.promote_last(usage.order(apps))
    return [(usage.count(a.desktop_id), a, []) for a in ordered]


def right_text(app, mode: str, usage: Usage, ranks: dict,
               show_generic: bool = False) -> str:
    """The right-hand column text for one app."""
    if mode == "off":
        return ""
    if mode == "exec":
        return app.generic_name if show_generic else app.exec_display
    if app.desktop_id == usage.last_id():
        return "was last"
    return "·" * dots_for(ranks.get(app.desktop_id, 999))


def last_name(apps, usage: Usage) -> str:
    """Display name of the most recently launched app, if still installed."""
    last_id = usage.last_id()
    if not last_id:
        return ""
    for app in apps:
        if app.desktop_id == last_id:
            return app.name
    return ""


def build_layout(*, apps, usage: Usage, query: str, config: dict,
                 prefix: str, selected: int = 0, scroll: int = 0,
                 metrics=None, cols: int = 100, command_argv=None) -> render.Layout:
    """Everything needed to paint the strip for the current state."""
    ui = config["ui"]
    mode = ui["right_column"]
    ranks = usage.frequency_ranks(apps)
    matches = browse_matches(query, apps, usage)

    rows = []
    if not matches and query.strip() and command_argv:
        rows.append(render.Row(name="", is_command=True, selected=True))
    else:
        for i, (_, app, hits) in enumerate(matches):
            rows.append(render.Row(
                name=app.name,
                right=right_text(app, mode, usage, ranks, ui["show_generic"]),
                hits=frozenset(hits),
                index=i + 1,
                selected=(i == selected),
            ))

    return render.Layout(
        rows=rows,
        selected=selected,
        total=len(matches) if matches else len(apps),
        query=query,
        prefix=prefix,
        command=query.strip(),
        last_name=last_name(apps, usage),
        metrics=metrics or render.Metrics(),
        cols=cols,
        max_rows=int(ui["max_rows"]),
        scroll=scroll,
        pad_x=int(ui["pad_x"]),
        show_index=bool(ui["show_index"]),
        right_column=mode,
        cursor_on=True,
    )


def render_text(layout: render.Layout, width: int | None = None) -> str:
    """The panel as plain text, framed -- what `--preview` prints."""
    cols = width or layout.cols
    layout.cols = cols
    rule = "─" * cols
    out = ["╭" + rule + "╮"]
    lines = layout.plain_lines()
    for i, line in enumerate(lines):
        out.append("│" + line.ljust(cols)[:cols] + "│")
        if i in (0, len(lines) - 2):
            out.append("├" + rule + "┤")
    out.append("╰" + rule + "╯")
    return "\n".join(out)
