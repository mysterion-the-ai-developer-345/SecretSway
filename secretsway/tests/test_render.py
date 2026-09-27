import pathlib
import unittest

from secretsway.render import (
    CURSOR, CURSOR_BLOCK, DIM, SEL_BG, SEL_FG, TEXT,
    Layout, Metrics, Row, Segment, clamp, ellipsize, move_index, scroll_for,
)


def make(rows, **kw):
    base = dict(
        rows=rows,
        metrics=Metrics(cell_w=8, cell_h=18, ascent=14),
        cols=80,
        max_rows=14,
        prefix="dev@sway ~]$",
    )
    base.update(kw)
    return Layout(**base)


SAMPLE = [
    Row(name="Firefox", right="/usr/bin/firefox", index=1, hits=frozenset({0})),
    Row(name="Firefox Developer Edition", right="flatpak run firefox", index=2,
        hits=frozenset({0, 8})),
    Row(name="Firefox Nightly", right="snap run firefox", index=3, selected=True,
        hits=frozenset({0})),
]


class TestEllipsize(unittest.TestCase):
    def test_short_string_untouched(self):
        self.assertEqual(ellipsize("abc", 5), "abc")

    def test_exact_fit_untouched(self):
        self.assertEqual(ellipsize("abcde", 5), "abcde")

    def test_long_string_marked(self):
        self.assertEqual(ellipsize("abcdefgh", 5), "abcd…")

    def test_width_one_is_just_the_ellipsis(self):
        self.assertEqual(ellipsize("abcdef", 1), "…")

    def test_zero_width_is_empty(self):
        self.assertEqual(ellipsize("abc", 0), "")

    def test_result_never_exceeds_width(self):
        for width in range(1, 12):
            self.assertLessEqual(len(ellipsize("abcdefghijklmno", width)), width)


class TestGeometry(unittest.TestCase):
    def test_height_counts_header_rows_and_status(self):
        layout = make(SAMPLE)
        self.assertEqual(layout.line_count, 5)
        self.assertEqual(layout.height, 5 * 18)

    def test_height_grows_with_results(self):
        few = make(SAMPLE[:1])
        many = make(SAMPLE * 4)
        self.assertGreater(many.height, few.height)

    def test_height_shrinks_to_nothing_when_empty(self):
        layout = make([])
        self.assertEqual(layout.line_count, 2)
        self.assertEqual(layout.height, 2 * 18)

    def test_max_rows_caps_the_list(self):
        layout = make(SAMPLE * 20, max_rows=5)
        self.assertEqual(len(layout.visible_rows()), 5)
        self.assertEqual(layout.line_count, 7)

    def test_zero_max_rows_shows_no_results(self):
        self.assertEqual(make(SAMPLE, max_rows=0).line_count, 2)

    def test_content_width_leaves_room_for_border_and_padding(self):
        layout = make(SAMPLE, cols=80, pad_x=2)
        self.assertEqual(layout.content_width(), 80 - 2 - 4)

    def test_narrow_window_still_has_usable_content(self):
        self.assertGreaterEqual(make(SAMPLE, cols=4, pad_x=2).content_width(), 8)

    def test_right_column_is_capped(self):
        rows = [Row(name="x", right="y" * 200)]
        self.assertLessEqual(make(rows).right_width(rows), 28)

    def test_right_column_zero_when_hidden(self):
        rows = [Row(name="x", right="y" * 40)]
        self.assertEqual(make(rows, right_column="off").right_width(rows), 0)

    def test_right_column_never_starves_the_name(self):
        rows = [Row(name="x", right="y" * 200)]
        layout = make(rows, cols=40)
        self.assertGreaterEqual(layout.right_width(rows), 0)
        self.assertLess(layout.right_width(rows), layout.content_width())


class TestHeader(unittest.TestCase):
    def test_prefix_prompt_and_query(self):
        line = make([], query="fire").header_line()
        self.assertEqual("".join(s.text for s in line), "dev@sway ~]$ > fire_")

    def test_cursor_can_be_hidden(self):
        line = make([], query="fire", cursor_on=False).header_line()
        self.assertNotIn(CURSOR_BLOCK, "".join(s.text for s in line))

    def test_prefix_is_dim_and_mark_is_accented(self):
        line = make([], query="").header_line()
        self.assertEqual(line[0].fg, DIM)
        self.assertEqual(line[0].text, "dev@sway ~]$ ")
        self.assertEqual(line[1].fg, CURSOR)
        self.assertEqual(line[1].text, "> ")

    def test_no_prefix_is_acceptable(self):
        line = make([], query="ls", prefix="").header_line()
        self.assertEqual("".join(s.text for s in line), "> ls_")


class TestRows(unittest.TestCase):
    def test_index_is_right_aligned(self):
        line = make(SAMPLE).row_line(SAMPLE[0], 17)
        self.assertTrue(line[0].text.startswith("  1 "))

    def test_index_disabled(self):
        line = make(SAMPLE, show_index=False).row_line(SAMPLE[0], 0)
        self.assertEqual(len(line), 1)
        self.assertEqual(line[0].text, "Firefox")

    def test_selection_inverts(self):
        layout = make(SAMPLE)
        line = layout.row_line(SAMPLE[2], 16)
        name_seg = line[1]
        self.assertEqual(name_seg.fg, SEL_FG)
        self.assertEqual(name_seg.bg, SEL_BG)

    def test_unselected_is_normal(self):
        line = make(SAMPLE).row_line(SAMPLE[0], 16)
        self.assertEqual(line[1].fg, TEXT)
        self.assertIsNone(line[1].bg)

    def test_hits_are_carried_through_for_highlighting(self):
        line = make(SAMPLE).row_line(SAMPLE[1], 0)
        self.assertEqual(line[1].hits, frozenset({0, 8}))

    def test_right_column_is_dim_when_unselected(self):
        line = make(SAMPLE).row_line(SAMPLE[0], 17)
        self.assertEqual(line[-1].fg, DIM)

    def test_line_never_exceeds_content_width(self):
        layout = make(SAMPLE, cols=60)
        right_w = layout.right_width(SAMPLE)
        for row in SAMPLE:
            line = layout.row_line(row, right_w)
            self.assertLessEqual(len("".join(s.text for s in line)),
                                 layout.content_width())

    def test_long_name_is_ellipsized_not_overflowing(self):
        row = Row(name="A" * 200, right="b" * 20, index=1)
        layout = make([row], cols=50)
        line = layout.row_line(row, layout.right_width([row]))
        self.assertLessEqual(len("".join(s.text for s in line)),
                             layout.content_width())
        self.assertIn("…", "".join(s.text for s in line))


class TestCommandFallbackLine(unittest.TestCase):
    def test_text(self):
        line = make([], command="git rebase -i", selected=0).command_line()
        self.assertIn("run as command: git rebase -i",
                      "".join(s.text for s in line))

    def test_is_inverted_when_selected(self):
        line = make([], command="ls", selected=0).command_line()
        self.assertEqual(line[1].bg, SEL_BG)

    def test_is_not_inverted_when_not_selected(self):
        line = make([], command="ls", selected=3).command_line()
        self.assertIsNone(line[1].bg)

    def test_very_long_command_is_ellipsized(self):
        layout = make([], command="x" * 500, selected=0)
        line = layout.command_line()
        self.assertLessEqual(len("".join(s.text for s in line)),
                             layout.content_width())


class TestStatusLine(unittest.TestCase):
    def test_shows_position_and_total(self):
        line = make(SAMPLE, total=187, selected=1).status_line()
        self.assertIn("2/187", "".join(s.text for s in line))

    def test_first_row_is_one(self):
        layout = make(SAMPLE, total=3, selected=0)
        self.assertIn("1/3", "".join(s.text for s in layout.status_line()))

    def test_empty_list_shows_zero(self):
        self.assertIn("0/0", "".join(s.text for s in make([]).status_line()))

    def test_hints_present(self):
        text = "".join(s.text for s in make(SAMPLE).status_line())
        self.assertIn("quit", text)

    def test_hints_are_ascii_only(self):
        """A missing glyph in a monospace font breaks the character grid."""
        from secretsway.render import HINTS
        self.assertTrue(HINTS.isascii(), f"non-ascii in hints: {HINTS!r}")
        self.assertNotIn("↵", HINTS)

    def test_decorations_are_ascii_only(self):
        """Same reason, for the prompt marker and the block cursor.

        U+25B8 and U+258C rendered as blanks or at the wrong width on the
        user's font, which both looked broken and pushed the grid out.
        """
        from secretsway.render import CURSOR_BLOCK, PROMPT_MARK
        self.assertTrue(PROMPT_MARK.isascii(), PROMPT_MARK)
        self.assertTrue(CURSOR_BLOCK.isascii(), CURSOR_BLOCK)
        self.assertNotIn("▸", PROMPT_MARK)
        self.assertNotIn("▌", CURSOR_BLOCK)


class TestDerivedGeometry(unittest.TestCase):
    """Layout.height is derived, so the window must never assign to it."""

    def test_constructible_with_no_rows(self):
        # The window builds its first Layout before it has any results, and
        # with guessed metrics before it has measured any.
        layout = Layout(prefix="dev@sway ~]$", cols=100)
        self.assertEqual(layout.rows, [])
        self.assertGreater(layout.height, 0)
        self.assertEqual(len(layout.lines()), 2)

    def test_constructible_with_only_a_prefix(self):
        self.assertEqual(Layout(prefix="x$").line_count, 2)

    def test_height_is_read_only(self):
        layout = make(SAMPLE)
        with self.assertRaises(AttributeError):
            layout.height = 100

    def test_height_follows_new_metrics(self):
        layout = make(SAMPLE, metrics=Metrics(cell_w=8, cell_h=18, ascent=14))
        self.assertEqual(layout.height, 90)
        layout.metrics = Metrics(cell_w=8, cell_h=20, ascent=16)
        self.assertEqual(layout.height, 100)

    def test_height_follows_new_cols_independently(self):
        # cols affects width only; height must not drift with it.
        narrow = make(SAMPLE, cols=40)
        wide = make(SAMPLE, cols=200)
        self.assertEqual(narrow.height, wide.height)

    def test_resolve_family_falls_back_without_pango(self):
        # No Pango here (no gi), so it must still hand back the first family.
        from secretsway.render import resolve_family
        self.assertEqual(resolve_family("Nope Mono, monospace"), "Nope Mono")
        self.assertEqual(resolve_family("  "), "monospace")


class TestWholePanel(unittest.TestCase):
    def test_line_count_matches_output(self):
        layout = make(SAMPLE)
        self.assertEqual(len(layout.lines()), layout.line_count)

    def test_plain_lines_renders_everything(self):
        text = "\n".join(make(SAMPLE, total=187, selected=2, query="fire").plain_lines())
        self.assertIn("dev@sway ~]$ > fire_", text)
        self.assertIn("Firefox", text)
        self.assertIn("3/187", text)
        self.assertIn("esc quit", text)

    def test_command_panel_has_only_the_fallback(self):
        layout = make([Row(name="", is_command=True)], command="htop",
                      total=0, selected=0)
        text = "\n".join(layout.plain_lines())
        self.assertIn("run as command: htop", text)
        self.assertNotIn("1 ", text)

    def test_nothing_is_selected_when_there_is_nothing(self):
        # Header and status remain; only the result list disappears.
        layout = make([])
        self.assertEqual(len(layout.lines()), 2)
        self.assertEqual(layout.selected, 0)

    def test_query_with_unicode_does_not_crash(self):
        text = "\n".join(make(SAMPLE, query="ñandú→").plain_lines())
        self.assertIn("ñandú→", text)

    def test_no_line_exceeds_the_window(self):
        for cols in (20, 40, 80, 200):
            layout = make(SAMPLE * 3, cols=cols, total=100, selected=1)
            for line in layout.plain_lines():
                self.assertLessEqual(len(line), layout.content_width(),
                                     f"overflowed at cols={cols}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
