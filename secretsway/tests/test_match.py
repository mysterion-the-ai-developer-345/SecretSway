import random
import unittest
from dataclasses import dataclass

from secretsway.match import match, rank


@dataclass
class FakeApp:
    name: str
    generic_name: str = ""
    exec_display: str = ""


class TestMatch(unittest.TestCase):
    def test_empty_pattern_is_a_match(self):
        self.assertEqual(match("", "anything"), (0, []))

    def test_longer_pattern_never_matches(self):
        self.assertIsNone(match("abcdef", "abc"))

    def test_non_subsequence_rejected(self):
        self.assertIsNone(match("xyz", "Firefox"))
        self.assertIsNone(match("foz", "Firefox"))

    def test_case_insensitive(self):
        self.assertIsNotNone(match("FIREFOX", "Firefox"))
        self.assertIsNotNone(match("firefox", "Firefox"))

    def test_exact_case_outranks_folded(self):
        exact = match("Firefox", "Firefox")[0]
        folded = match("firefox", "Firefox")[0]
        self.assertGreater(exact, folded)

    def test_consecutive_beats_scattered(self):
        # "ff" should take the adjacent f's in "Firefox", not f-then-somewhere-f.
        firefox = match("ff", "Firefox")
        scattered = match("fx", "Firefox")
        self.assertGreater(firefox[0], scattered[0])

    def test_prefix_beats_mid_string(self):
        self.assertGreater(match("fire", "Firefox")[0], match("fox", "Firefox")[0])

    def test_word_boundary_bonus(self):
        # "t" as a word start in "Text Editor" beats "t" buried inside a word.
        boundary = match("t", "Text Editor")[0]
        buried = match("t", "Editor")[0]
        self.assertGreater(boundary, buried)

    def test_indices_are_ascending_and_match_the_pattern(self):
        """The strongest invariant: whatever we return must be renderable."""
        cases = [
            ("ff", "Firefox"),
            ("gimp", "GNU Image Manipulation Program"),
            ("fde", "Firefox Developer Edition"),
            ("term", "foot terminal"),
            ("ace", "a b c d e"),
        ]
        for pattern, haystack in cases:
            with self.subTest(pattern=pattern, haystack=haystack):
                result = match(pattern, haystack)
                self.assertIsNotNone(result)
                _, indices = result
                self.assertEqual(len(indices), len(pattern))
                self.assertEqual(indices, sorted(indices))
                self.assertEqual(len(set(indices)), len(indices))
                for char, index in zip(pattern, indices):
                    self.assertEqual(haystack[index].lower(), char.lower())

    def test_indices_stay_in_bounds(self):
        rng = random.Random(1234)
        alphabet = "abcdefghijklmnopqrstuvwxyz ._-"
        for _ in range(2000):
            haystack = "".join(rng.choices(alphabet, k=rng.randint(1, 30)))
            pattern = "".join(rng.choices(alphabet, k=rng.randint(1, 5)))
            result = match(pattern, haystack)
            if result is None:
                continue
            _, indices = result
            for index in indices:
                self.assertTrue(0 <= index < len(haystack))

    def test_every_returned_match_is_really_a_subsequence(self):
        """Guards against a False that looks like a match to the renderer."""
        rng = random.Random(99)
        for _ in range(2000):
            haystack = "".join(rng.choices("abcde", k=rng.randint(1, 12)))
            pattern = "".join(rng.choices("abcde", k=rng.randint(1, 4)))
            result = match(pattern, haystack)
            if result is None:
                continue
            _, indices = result
            got = "".join(haystack[i] for i in indices).lower()
            self.assertEqual(got, pattern.lower())


class TestRank(unittest.TestCase):
    def setUp(self):
        self.apps = [
            FakeApp("Firefox", "Web Browser", "/usr/bin/firefox"),
            FakeApp("Firefox Developer Edition", "Web Browser", "flatpak run firefox"),
            FakeApp("Firefox Nightly", "Web Browser", "snap run firefox"),
            FakeApp("GIMP", "Image Manipulation Program", "gimp"),
            FakeApp("foot", "Terminal Emulator", "foot"),
        ]

    def test_prefix_match_sorts_first(self):
        names = [a.name for _, a, _ in rank("fire", self.apps)]
        self.assertEqual(names[0], "Firefox")

    def test_shorter_name_wins_for_same_prefix(self):
        names = [a.name for _, a, _ in rank("fire", self.apps)]
        self.assertLess(names.index("Firefox"), names.index("Firefox Nightly"))

    def test_generic_name_hit_ranks_below_name_hit(self):
        # Firefox has no "web" in its name, but its GenericName is "Web Browser".
        # A rival that matches by name outright must outrank it.
        apps = self.apps + [FakeApp("Webmail", "Mail Client", "thunderbird")]
        ranked = rank("web", apps)
        self.assertEqual(ranked[0][1].name, "Webmail", "name hit must outrank generic hit")
        self.assertNotEqual(ranked[0][2], [], "a name hit should carry indices")
        below = [a.name for _, a, _ in ranked]
        self.assertIn("Firefox", below, "generic-name hit must still be findable")
        self.assertEqual(below.index("Webmail"), 0)

    def test_exec_hit_is_findable(self):
        ranked = rank("flatpak", self.apps)
        self.assertTrue(ranked)
        self.assertEqual(ranked[0][1].name, "Firefox Developer Edition")
        self.assertEqual(ranked[0][2], [], "secondary hits must not fake name indices")

    def test_empty_query_returns_everything_in_order(self):
        ranked = rank("", self.apps)
        self.assertEqual(len(ranked), len(self.apps))
        self.assertEqual([a.name for _, a, _ in ranked], [a.name for a in self.apps])

    def test_ordering_is_stable(self):
        first = [a.name for _, a, _ in rank("fire", self.apps)]
        for _ in range(20):
            self.assertEqual([a.name for _, a, _ in rank("fire", self.apps)], first)

    def test_no_match_returns_empty(self):
        self.assertEqual(rank("qqqqzzz", self.apps), [])

    def test_name_match_always_outranks_metadata_match(self):
        """The guarantee `rank` promises, checked across many query/app pairs.

        A tight GenericName match ("fl" in "File Manager") used to outrank a
        real name match ("fl" in "Firefox Developer Edition").  Secondary hits
        must now sit strictly below every primary hit.
        """
        apps = [
            FakeApp("Thunar", "File Manager", "thunar"),
            FakeApp("Firefox Developer Edition", "Web Browser", "flatpak run firefox"),
            FakeApp("GIMP", "Image Manipulation Program", "gimp"),
            FakeApp("Foot", "Terminal Emulator", "foot"),
            FakeApp("LibreOffice Writer", "Word Processor", "lowriter"),
        ]
        queries = [
            "fl", "f", "file", "mgr", "th", "gim", "term", "word", "ff",
            "gm", "libre", "office", "w", "t", "foot", "im", "proc", "e",
        ]
        for query in queries:
            with self.subTest(query=query):
                ranked = rank(query, apps)
                if not ranked:
                    continue
                is_primary = [match(query, a.name) is not None for _, a, _ in ranked]
                if not any(is_primary):
                    continue
                # Every name hit must precede every metadata hit.
                self.assertEqual(
                    is_primary,
                    sorted(is_primary, reverse=True),
                    f"a metadata hit outranked a name hit for {query!r}",
                )

    def test_secondary_relative_order_is_preserved(self):
        apps = [
            FakeApp("Alpha", "File Manager", ""),
            FakeApp("Beta", "File Manager", ""),
        ]
        ranked = rank("file", apps)
        self.assertEqual([a.name for _, a, _ in ranked], ["Alpha", "Beta"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
