"""Fuzzy matcher, in the style of fzf's v1 algorithm.

Pure stdlib and deliberately free of any GTK import, so it can be unit-tested
without a compositor.  See tests/test_match.py.

The scorer is a greedy forward pass to prove the pattern is a subsequence, a
backward pass to tighten the match to its minimal span, then a forward pass that
accumulates bonuses.  That middle pass is what makes "ff" prefer the two f's in
"Firefox" over the f in "F" followed by some distant f -- matching characters are
pulled as far right as they can go without breaking the subsequence, which
minimises the span and therefore maximises the bonus.
"""

from __future__ import annotations

# Scoring constants, taken from fzf v1's algorithm.
SCORE_MATCH = 16
SCORE_GAP_START = -3
SCORE_GAP_EXTENSION = -1
BONUS_BOUNDARY = SCORE_MATCH // 2            # 8
BONUS_CONSECUTIVE = -(SCORE_GAP_START + SCORE_GAP_EXTENSION)  # 4
BONUS_FIRST_CHAR_MULTIPLIER = 2

# Applied to matches that only land because we also searched GenericName/Exec.
SECONDARY_PENALTY = 0.45


def _is_word(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _bonus(prev: str | None, cur: str) -> int:
    """Reward matches at word starts and case humps."""
    if prev is None:
        return BONUS_BOUNDARY * BONUS_FIRST_CHAR_MULTIPLIER
    if prev.islower() and cur.isupper():
        return BONUS_BOUNDARY
    if not _is_word(prev) and _is_word(cur):
        return BONUS_BOUNDARY
    return 0


def match(pattern: str, haystack: str) -> tuple[int, list[int]] | None:
    """Score `pattern` against `haystack`.

    Returns ``(score, indices)`` where indices are the positions in haystack that
    the pattern characters matched, in ascending order -- the renderer uses those
    to highlight.  Returns None when `pattern` is not a subsequence of `haystack`.
    An empty pattern is a match with score 0 and no indices.
    """
    plen, hlen = len(pattern), len(haystack)
    if plen == 0:
        return 0, []
    if plen > hlen:
        return None

    pl = pattern.lower()
    hl = haystack.lower()

    # Pass 1: greedy forward, proves it is a subsequence at all.
    first = []
    j = 0
    for pc in pl:
        found = hl.find(pc, j)
        if found < 0:
            return None
        first.append(found)
        j = found + 1

    # Pass 2: pull each position as far right as the subsequence still allows.
    # This is the "tighten to minimal span" step from fzf v1.
    tight = [0] * plen
    end = first[plen - 1]
    for i in range(plen - 1, -1, -1):
        k = hl.rfind(pl[i], 0, end + 1)
        if k < first[i]:
            k = first[i]
        tight[i] = k
        end = k - 1

    # Pass 3: walk the tightened positions, re-anchoring to the leftmost run so
    # consecutive characters actually sit next to each other.
    score = 0
    indices: list[int] = []
    prev_pos = -1
    for i, pc in enumerate(pl):
        k = max(tight[i], prev_pos + 1)
        # k must still be able to leave room for the rest of the pattern.
        if k + (plen - 1 - i) > hlen:
            k = prev_pos + 1
        prev_char = haystack[k - 1] if k > 0 else None
        score += SCORE_MATCH
        score += _bonus(prev_char, haystack[k])
        if k == prev_pos + 1 and i > 0:
            score += BONUS_CONSECUTIVE
        else:
            score += SCORE_GAP_START
            score += SCORE_GAP_EXTENSION * max(0, k - prev_pos - 1)
        indices.append(k)
        prev_pos = k
        # Both `pl` and `hl` are already lowercased, so this must compare the
        # originals to detect that the hit was case-folded rather than exact.
        if haystack[k] != pattern[i]:
            score -= 1

    return score, indices


def _secondary_fields(app) -> list[str]:
    """Fields to fall back on when the name itself does not match."""
    fields = []
    if getattr(app, "generic_name", ""):
        fields.append(app.generic_name)
    if getattr(app, "exec_display", ""):
        fields.append(app.exec_display)
    return fields


def rank(query: str, apps) -> list[tuple[int, object, list[int]]]:
    """Rank `apps` against `query`, best first.

    Each result is ``(score, app, indices)``.  `indices` indexes into
    ``app.name`` when the match came from the name; for a secondary match it is
    empty, since highlighting an offset inside a concatenated field would be
    meaningless.

    One hard rule: **any hit in an app's name outranks any hit in its
    GenericName or Exec**, no matter how tight the secondary match is.  A fixed
    multiplier cannot express that on its own -- "fl" against Thunar's Generic
    Name "File Manager" scores far higher than "fl" against "Firefox Developer
    Edition".  Sorting the two tiers separately and concatenating enforces the
    rule exactly, without needing to bend the scores into a common range (which
    is not possible anyway: a sparse name match can score negative).

    Ties are broken by name so the ordering is stable between invocations --
    otherwise the same query would shuffle rows on every launch.
    """
    if not query:
        return [(0, app, []) for app in apps]

    primaries: list[tuple[int, object, list[int]]] = []
    secondaries: list[tuple[int, object]] = []

    for app in apps:
        scored = match(query, app.name)
        if scored is not None:
            primaries.append((scored[0], app, scored[1]))
            continue

        best = None
        for field in _secondary_fields(app):
            secondary = match(query, field)
            if secondary is None:
                continue
            candidate = int(secondary[0] * SECONDARY_PENALTY)
            if best is None or candidate > best:
                best = candidate
        if best is not None:
            secondaries.append((best, app))

    # Each tier is ordered among itself, then the tiers are concatenated.  Ties
    # fall back to name so repeated invocations give an identical ordering.
    primaries.sort(key=lambda h: (-h[0], h[1].name.lower()))
    secondaries.sort(key=lambda h: (-h[0], h[1].name.lower()))
    return primaries + [(score, app, []) for score, app in secondaries]
