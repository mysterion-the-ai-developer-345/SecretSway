"""The launch-history memory: persistence, ordering, and dot scale.

This is what makes the launcher put the things you actually open at the top,
so the failure modes worth guarding are a corrupt file stopping the launcher
from opening, a half-written file losing history, and the ordering silently
becoming unstable between runs.
"""

import json
import os
import shutil
import tempfile
import unittest
from dataclasses import dataclass

from secretsway.usage import MAX_DOTS, Usage, dots_for


@dataclass
class FakeApp:
    name: str
    desktop_id: str = ""


def apps(*names):
    return [FakeApp(name=n, desktop_id=n.lower().replace(" ", "-")) for n in names]


class UsageFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="secretsway-usage-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "usage.json")

    def fresh(self, counts=None, last=""):
        return Usage(counts=counts or {}, last=last, path=self.path)


class TestDots(unittest.TestCase):
    def test_first_place_gets_the_maximum(self):
        self.assertEqual(dots_for(0), MAX_DOTS)

    def test_second_place_gets_one_fewer(self):
        self.assertEqual(dots_for(1), 5)

    def test_each_rank_loses_exactly_one(self):
        self.assertEqual([dots_for(i) for i in range(5)], [6, 5, 4, 3, 2])

    def test_every_app_keeps_at_least_one_dot(self):
        for rank in range(0, 500):
            self.assertGreaterEqual(dots_for(rank), 1)

    def test_every_app_keeps_at_least_one_dot_negative_rank(self):
        self.assertEqual(dots_for(-5), MAX_DOTS)

    def test_deep_ranks_flatten_to_one(self):
        self.assertEqual(dots_for(50), 1)

    def test_custom_maximum(self):
        self.assertEqual([dots_for(i, 3) for i in range(4)], [3, 2, 1, 1])


class TestRecording(UsageFixture):
    def test_first_launch_sets_last_and_count(self):
        usage = self.fresh()
        usage.record("firefox")
        self.assertEqual(usage.count("firefox"), 1)
        self.assertEqual(usage.last_id(), "firefox")

    def test_repeat_launches_accumulate(self):
        usage = self.fresh()
        for _ in range(5):
            usage.record("firefox")
        self.assertEqual(usage.count("firefox"), 5)

    def test_last_tracks_the_most_recent(self):
        usage = self.fresh()
        usage.record("firefox")
        usage.record("gimp")
        self.assertEqual(usage.last_id(), "gimp")

    def test_empty_id_is_ignored(self):
        usage = self.fresh()
        usage.record("")
        self.assertEqual(usage.total(), 0)

    def test_total_sums_everything(self):
        usage = self.fresh()
        usage.record("a")
        usage.record("a")
        usage.record("b")
        self.assertEqual(usage.total(), 3)

    def test_unknown_apps_count_zero(self):
        self.assertEqual(self.fresh().count("never-seen"), 0)


class TestPersistence(UsageFixture):
    def test_round_trip(self):
        usage = self.fresh()
        usage.record("firefox")
        usage.record("gimp")
        self.assertTrue(usage.save())

        reloaded = Usage.load(self.path)
        self.assertEqual(reloaded.count("firefox"), 1)
        self.assertEqual(reloaded.count("gimp"), 1)
        self.assertEqual(reloaded.last_id(), "gimp")

    def test_missing_file_is_empty_not_an_error(self):
        usage = Usage.load(os.path.join(self.dir, "nope.json"))
        self.assertEqual(usage.total(), 0)
        self.assertEqual(usage.last_id(), "")

    def test_corrupt_file_is_ignored(self):
        with open(self.path, "w") as handle:
            handle.write("{not json at all")
        self.assertEqual(Usage.load(self.path).total(), 0)

    def test_truncated_file_is_ignored(self):
        with open(self.path, "w") as handle:
            handle.write('{"version": 1, "counts": {"a"')
        self.assertEqual(Usage.load(self.path).total(), 0)

    def test_empty_file_is_ignored(self):
        with open(self.path, "w"):
            pass
        self.assertEqual(Usage.load(self.path).total(), 0)

    def test_wrong_types_are_ignored(self):
        with open(self.path, "w") as handle:
            json.dump({"version": 1, "last": 42, "counts": "nope"}, handle)
        usage = Usage.load(self.path)
        self.assertEqual(usage.total(), 0)
        self.assertEqual(usage.last_id(), "")

    def test_non_numeric_counts_are_dropped(self):
        with open(self.path, "w") as handle:
            json.dump({"counts": {"a": 3, "b": "many", "c": None}}, handle)
        usage = Usage.load(self.path)
        self.assertEqual(usage.count("a"), 3)
        self.assertEqual(usage.count("b"), 0)

    def test_json_array_is_ignored(self):
        with open(self.path, "w") as handle:
            json.dump([1, 2, 3], handle)
        self.assertEqual(Usage.load(self.path).total(), 0)

    def test_save_creates_missing_directories(self):
        usage = Usage(path=os.path.join(self.dir, "a", "b", "c", "usage.json"))
        usage.record("x")
        self.assertTrue(usage.save())
        self.assertTrue(os.path.exists(usage.path))

    def test_save_leaves_no_temp_files(self):
        usage = self.fresh()
        usage.record("x")
        usage.save()
        leftovers = [n for n in os.listdir(self.dir) if n.startswith(".usage-")]
        self.assertEqual(leftovers, [], "atomic write left a temp file behind")

    def test_save_to_unwritable_path_returns_false(self):
        usage = Usage(path="/proc/definitely/not/writable.json")
        self.assertFalse(usage.save())

    def test_save_does_not_corrupt_the_old_file_on_failure(self):
        usage = self.fresh()
        usage.record("good")
        usage.save()
        with open(self.path) as handle:
            before = handle.read()
        usage.path = "/proc/nope/usage.json"
        usage.save()
        with open(self.path) as handle:
            self.assertEqual(handle.read(), before)


class TestOrdering(UsageFixture):
    def setUp(self):
        super().setUp()
        self.catalogue = apps("Firefox", "Gimp", "Foot", "Thunar", "Nvim")

    def test_most_used_first(self):
        usage = self.fresh({"firefox": 10, "gimp": 3})
        ordered = usage.order(self.catalogue)
        self.assertEqual([a.name for a in ordered][:2], ["Firefox", "Gimp"])

    def test_never_used_apps_come_last(self):
        usage = self.fresh({"nvim": 1})
        ordered = usage.order(self.catalogue)
        names = [a.name for a in ordered]
        self.assertEqual(names[0], "Nvim", "the one used app should lead")
        # Everything unused follows, in name order.
        self.assertEqual(names[1:], ["Firefox", "Foot", "Gimp", "Thunar"])

    def test_unused_apps_stay_alphabetical(self):
        usage = self.fresh()
        ordered = usage.order(self.catalogue)
        # Case-insensitive, so Foot precedes Gimp.
        self.assertEqual(
            [a.name for a in ordered], ["Firefox", "Foot", "Gimp", "Nvim", "Thunar"]
        )

    def test_ordering_is_stable_across_calls(self):
        usage = self.fresh({"foot": 2, "gimp": 2, "firefox": 1})
        first = [a.name for a in usage.order(self.catalogue)]
        for _ in range(20):
            self.assertEqual([a.name for a in usage.order(self.catalogue)], first)

    def test_frequency_ranks_cover_everything(self):
        usage = self.fresh({"gimp": 5})
        ranks = usage.frequency_ranks(self.catalogue)
        self.assertEqual(len(ranks), len(self.catalogue))
        self.assertEqual(ranks["gimp"], 0)

    def test_ranks_ignore_the_current_filter(self):
        """Dots must mean the same thing whatever you have typed."""
        usage = self.fresh({"gimp": 5, "firefox": 4, "foot": 3})
        full = usage.frequency_ranks(self.catalogue)
        subset = usage.frequency_ranks(apps("Gimp", "Firefox"))
        self.assertEqual(full["gimp"], subset["gimp"])


class TestPromoteLast(UsageFixture):
    def setUp(self):
        super().setUp()
        self.catalogue = apps("Firefox", "Gimp", "Foot", "Thunar")

    def test_last_is_moved_to_the_front(self):
        usage = self.fresh({"thunar": 1}, last="thunar")
        ordered = usage.promote_last(usage.order(self.catalogue))
        self.assertEqual(ordered[0].name, "Thunar")

    def test_already_first_stays_put(self):
        usage = self.fresh({"gimp": 9}, last="gimp")
        ordered = usage.promote_last(usage.order(self.catalogue))
        self.assertEqual(ordered[0].name, "Gimp")
        self.assertEqual(len(ordered), len(self.catalogue))

    def test_nothing_lost_when_promoting(self):
        usage = self.fresh({"thunar": 1}, last="thunar")
        ordered = usage.promote_last(usage.order(self.catalogue))
        self.assertEqual(len(ordered), len(self.catalogue))
        self.assertEqual({a.name for a in ordered}, {a.name for a in self.catalogue})

    def test_no_history_leaves_order_untouched(self):
        usage = self.fresh()
        before = [a.name for a in usage.order(self.catalogue)]
        after = [a.name for a in usage.promote_last(usage.order(self.catalogue))]
        self.assertEqual(before, after)

    def test_uninstalled_app_is_ignored(self):
        usage = self.fresh({}, last="some-removed-app")
        ordered = usage.promote_last(usage.order(self.catalogue))
        self.assertEqual(len(ordered), len(self.catalogue))

    def test_empty_catalogue_is_safe(self):
        self.assertEqual(self.fresh(last="x").promote_last([]), [])

    def test_promoting_does_not_mutate_the_input(self):
        usage = self.fresh({"thunar": 1}, last="thunar")
        original = usage.order(self.catalogue)
        before = [a.name for a in original]
        usage.promote_last(original)
        self.assertEqual([a.name for a in original], before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
