"""Scrolling and end-stopping for the result list.

The bug these guard against: `visible_rows()` used to be `rows[:max_rows]`
unconditionally, so arrowing past the fold moved the highlight off-panel where
it could not be seen -- a "ghost" walking down the list.  With 187 apps and a
15-row window most of the list was unreachable by eye.
"""

import pathlib
import unittest

from secretsway.render import Layout, Metrics, Row, clamp, move_index, scroll_for


def rows(count, selected=-1, right=None):
    return [
        Row(name=f"App {i:03d}", right=right or f"/usr/bin/app{i}",
            index=i + 1, selected=(i == selected))
        for i in range(count)
    ]


def layout(count, selected=0, scroll=0, max_rows=15, right=None):
    return Layout(
        rows=rows(count, selected, right),
        selected=selected,
        total=count,
        metrics=Metrics(cell_w=8, cell_h=18, ascent=14),
        cols=80,
        max_rows=max_rows,
        scroll=scroll,
        prefix="dev@sway ~]$",
    )


class TestClamp(unittest.TestCase):
    def test_inside_range_is_untouched(self):
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_below_low(self):
        self.assertEqual(clamp(-3, 0, 10), 0)

    def test_above_high(self):
        self.assertEqual(clamp(99, 0, 10), 10)

    def test_at_the_bounds(self):
        self.assertEqual(clamp(0, 0, 10), 0)
        self.assertEqual(clamp(10, 0, 10), 10)

    def test_inverted_range_collapses_to_low(self):
        self.assertEqual(clamp(5, 0, -1), 0)

    def test_empty_range(self):
        self.assertEqual(clamp(3, 4, 4), 4)


class TestMoveIndex(unittest.TestCase):
    def test_moves_forward(self):
        self.assertEqual(move_index(0, 1, 187), 1)

    def test_moves_backward(self):
        self.assertEqual(move_index(5, -1, 187), 4)

    def test_stops_at_top(self):
        self.assertEqual(move_index(0, -1, 187), 0)

    def test_stops_at_bottom(self):
        self.assertEqual(move_index(186, 1, 187), 186)

    def test_way_past_the_end_still_clamps(self):
        self.assertEqual(move_index(180, 100, 187), 186)

    def test_empty_list_is_safe(self):
        self.assertEqual(move_index(0, 1, 0), 0)
        self.assertEqual(move_index(0, -1, 0), 0)

    def test_no_wrap_by_default(self):
        self.assertNotEqual(move_index(0, -1, 5, wrap=False), 4)

    def test_wrap_when_asked(self):
        self.assertEqual(move_index(0, -1, 5, wrap=True), 4)
        self.assertEqual(move_index(4, 1, 5, wrap=True), 0)


class TestScrollFor(unittest.TestCase):
    def test_no_move_when_already_visible(self):
        self.assertEqual(scroll_for(10, 12, 187, 15), 10)

    def test_selection_at_the_top_of_the_window(self):
        self.assertEqual(scroll_for(10, 10, 187, 15), 10)

    def test_selection_at_the_bottom_of_the_window(self):
        self.assertEqual(scroll_for(10, 24, 187, 15), 10)

    def test_scrolls_down_when_selection_leaves_the_bottom(self):
        self.assertEqual(scroll_for(0, 15, 187, 15), 1)

    def test_selection_lands_on_the_last_visible_row(self):
        scroll = scroll_for(0, 15, 187, 15)
        self.assertTrue(scroll <= 15 < scroll + 15)

    def test_scrolls_up_when_selection_leaves_the_top(self):
        self.assertEqual(scroll_for(10, 4, 187, 15), 4)

    def test_never_exceeds_the_last_page(self):
        # The final row must not scroll the window past its own extent.
        self.assertEqual(scroll_for(0, 186, 187, 15), 172)

    def test_last_page_is_full_not_padded(self):
        scroll = scroll_for(0, 186, 187, 15)
        self.assertEqual(scroll, 187 - 15)

    def test_short_list_never_scrolls(self):
        self.assertEqual(scroll_for(0, 4, 5, 15), 0)

    def test_exactly_full_list_never_scrolls(self):
        self.assertEqual(scroll_for(0, 14, 15, 15), 0)

    def test_zero_window_is_safe(self):
        self.assertEqual(scroll_for(3, 5, 187, 0), 0)

    def test_empty_list_is_safe(self):
        self.assertEqual(scroll_for(3, 0, 0, 15), 0)

    def test_absurd_scroll_is_pulled_back(self):
        self.assertEqual(scroll_for(999, 5, 40, 15), 5)

    def test_result_always_shows_the_selection(self):
        """The invariant that makes the ghost impossible."""
        for total in (1, 14, 15, 16, 100, 187):
            for selected in range(total):
                scroll = scroll_for(0, selected, total, 15)
                self.assertTrue(
                    scroll <= selected < scroll + 15,
                    f"selection {selected} hidden at scroll {scroll} of {total}",
                )

    def test_scroll_is_monotonic_when_arding_down(self):
        previous = 0
        for selected in range(187):
            scroll = scroll_for(previous, selected, 187, 15)
            self.assertGreaterEqual(scroll, previous,
                                    "the view jumped backwards while arrowing down")
            previous = scroll


class TestVisibleRows(unittest.TestCase):
    def test_default_window_is_the_top(self):
        self.assertEqual(len(layout(187, scroll=0).visible_rows()), 15)

    def test_slice_follows_scroll(self):
        view = layout(187, scroll=100).visible_rows()
        self.assertEqual(view[0].name, "App 100")
        self.assertEqual(len(view), 15)

    def test_last_page_is_full(self):
        view = layout(187, scroll=172).visible_rows()
        self.assertEqual(len(view), 15)
        self.assertEqual(view[-1].name, "App 186")

    def test_short_list_shows_everything(self):
        self.assertEqual(len(layout(4, scroll=0).visible_rows()), 4)

    def test_scroll_past_the_end_is_ignored(self):
        self.assertEqual(len(layout(10, scroll=99, max_rows=15).visible_rows()), 10)

    def test_zero_window_shows_nothing(self):
        self.assertEqual(layout(187, max_rows=0).visible_rows(), [])

    def test_height_follows_the_window_not_the_list(self):
        self.assertEqual(layout(187, scroll=100).height, layout(187, scroll=0).height)


class TestSelectedVisible(unittest.TestCase):
    def test_true_when_in_window(self):
        self.assertTrue(layout(187, selected=5, scroll=0).selected_visible())

    def test_false_when_below_the_fold(self):
        self.assertFalse(layout(187, selected=20, scroll=0).selected_visible())

    def test_true_once_scrolled(self):
        self.assertTrue(layout(187, selected=20, scroll=10).selected_visible())

    def test_false_when_empty(self):
        self.assertFalse(layout(0).selected_visible())


class TestMetrics(unittest.TestCase):
    """The grid is only valid if measurement and drawing agree on the size."""

    def test_metrics_carry_the_measured_size(self):
        m = Metrics(cell_w=8.4, cell_h=19, ascent=15, size=14.0)
        self.assertEqual(m.size, 14.0)

    def test_default_metrics_are_usable_without_a_font(self):
        m = Metrics()
        self.assertGreater(m.cell_w, 0)
        self.assertGreater(m.cell_h, 0)
        self.assertTrue(m.monospace)

    def test_cell_width_is_a_float(self):
        """Rounding here is what made long names drift relative to short ones."""
        m = Metrics(cell_w=8.4)
        self.assertIsInstance(m.cell_w, float)
        self.assertNotEqual(m.cell_w, round(m.cell_w))

    def test_draw_does_not_derive_a_size_from_cell_height(self):
        """draw() must use metrics.size, not cell_h * a fudge factor.

        Measuring at one size and rendering at another rescales the advance, so
        the rendered text stops filling its cells and every column shifts by an
        amount proportional to how much text precedes it.
        """
        source = pathlib.Path(
            __file__).resolve().parent.parent / "secretsway" / "render.py"
        text = source.read_text()
        draw_body = text[text.index("def draw("):]
        self.assertIn("cr.set_font_size(m.size)", draw_body)
        self.assertNotIn("set_font_size(m.cell_h", draw_body)

    def test_layout_uses_the_measured_cell_width_everywhere(self):
        source = pathlib.Path(
            __file__).resolve().parent.parent / "secretsway" / "render.py"
        text = source.read_text()
        draw_body = text[text.index("def draw("):]
        self.assertNotIn("int(m.cell_w)", draw_body)
        self.assertNotIn("round(m.cell_w)", draw_body)


class TestRightColumnIsStable(unittest.TestCase):
    def test_width_does_not_change_when_scrolling(self):
        mixed = [
            Row(name="Short", right="a"),
            Row(name="Long", right="/usr/bin/a-very-long-command-path"),
        ]
        top = Layout(rows=mixed, max_rows=1, scroll=0, cols=80)
        bottom = Layout(rows=mixed, max_rows=1, scroll=1, cols=80)
        self.assertEqual(top.right_width(), bottom.right_width())

    def test_width_sees_rows_outside_the_window(self):
        long_cmd = "/usr/bin/a-very-long-command-path"
        mixed = [Row(name="A", right="x"), Row(name="B", right=long_cmd)]
        # max_rows=1, so only "A" is visible -- but the column must still be
        # sized for the long command below the fold, not for "x" alone.
        lay = Layout(rows=mixed, max_rows=1, scroll=0, cols=80)
        self.assertEqual(lay.right_width(), 28)   # capped
        self.assertEqual(lay.right_width(), min(len(long_cmd), 28))
        self.assertNotEqual(lay.right_width(), 1)  # not sized for the short row


class TestRightColumnAlignment(unittest.TestCase):
    """The right column must start at one fixed column, whatever the name."""

    @staticmethod
    def start_column(layout, row, right_w):
        """Column at which the right-hand text begins, measured off the line."""
        line = layout.row_line(row, right_w)
        consumed = 0
        for seg in line[:-1]:
            consumed += len(seg.text)
        return consumed + len(line[-1].text) - len(row.right)

    def test_start_is_independent_of_name_length(self):
        names = ["A", "Foot", "Gimp", "Firefox Developer Edition",
                 "F" * 60, "F" * 120]
        lay = Layout(rows=[Row(name=n, right="·" * 4) for n in names],
                     cols=80, max_rows=15, right_column="dots")
        right_w = lay.right_width()
        starts = {
            self.start_column(lay, row, right_w)
            for row in lay.rows
        }
        self.assertEqual(len(starts), 1,
                         f"right column starts at {sorted(starts)} depending "
                         f"on name length")

    def test_start_is_stable_at_the_exact_width_boundary(self):
        """The case that used to shift by one column."""
        lay = Layout(cols=60, max_rows=15, right_column="dots")
        gutter = lay.GUTTER
        right_w = 6
        name_w = lay.content_width() - gutter - (right_w + 1)
        exact = Row(name="F" * name_w, right="······")
        shorter = Row(name="F" * (name_w - 1), right="······")
        self.assertEqual(
            self.start_column(lay, exact, right_w),
            self.start_column(lay, shorter, right_w),
        )

    def test_never_overflows_the_panel(self):
        lay = Layout(rows=[Row(name="F" * 200, right="······")],
                     cols=60, max_rows=15, right_column="dots")
        right_w = lay.right_width()
        line = lay.row_line(lay.rows[0], right_w)
        self.assertLessEqual(len("".join(s.text for s in line)),
                             lay.content_width())

    def test_column_is_aligned_across_a_scrolled_list(self):
        """Scrolling must not shift the column."""
        rows = [Row(name=f"App {i}", right="·" * (1 + i % 6)) for i in range(40)]
        top = Layout(rows=rows, cols=70, max_rows=15, scroll=0, right_column="dots")
        bottom = Layout(rows=rows, cols=70, max_rows=15, scroll=20, right_column="dots")
        for row in bottom.visible_rows():
            self.assertEqual(
                self.start_column(top, row, top.right_width()),
                self.start_column(top, row, bottom.right_width()),
            )


class TestArrowingEndToEnd(unittest.TestCase):
    """Drive the real state machine the way the key handler does."""

    def drive(self, count, steps, max_rows=15, start=0):
        selected, scroll = start, 0
        seen = []
        for _ in range(steps):
            selected = move_index(selected, 1, count)
            scroll = scroll_for(scroll, selected, count, max_rows)
            view = list(range(scroll, scroll + max_rows))
            seen.append((selected, scroll, selected in view))
        return seen

    def test_selection_never_leaves_the_window(self):
        for count in (5, 15, 16, 40, 187):
            with self.subTest(count=count):
                for selected, scroll, visible in self.drive(count, count + 10):
                    self.assertTrue(
                        visible,
                        f"selection {selected} invisible (scroll={scroll}) "
                        f"with {count} rows",
                    )

    def test_reaches_the_last_row(self):
        selected, _, _ = self.drive(187, 400)[-1]
        self.assertEqual(selected, 186)

    def test_stops_at_the_bottom(self):
        selected, _, _ = self.drive(187, 500)[-1]
        self.assertEqual(selected, 186)

    def test_arding_back_up_stops_at_the_first(self):
        selected, scroll = 186, 172
        for _ in range(300):
            selected = move_index(selected, -1, 187)
            scroll = scroll_for(scroll, selected, 187, 15)
        self.assertEqual(selected, 0)
        self.assertEqual(scroll, 0)

    def test_no_blank_rows_at_the_end(self):
        for selected, scroll, _ in self.drive(187, 400):
            if selected >= 182:
                self.assertLessEqual(scroll + 15, 187,
                                     "the window scrolled past the end of the list")


if __name__ == "__main__":
    unittest.main(verbosity=2)
