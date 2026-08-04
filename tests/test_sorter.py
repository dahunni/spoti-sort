"""The reorder plan is the part that must not be wrong, so it gets real tests.

Every case checks the plan by replaying it through `apply_move`, which mirrors the
Spotify `insert_before` semantics exactly. If the local model and the API ever
disagree about what a move means, these fail.
"""

import random
import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spotisort.sorter import (NEWEST_FIRST, OLDEST_FIRST, apply_move,
                              plan_moves)


def replay(added_ats, moves):
    items = list(range(len(added_ats)))
    for range_start, insert_before in moves:
        items = apply_move(items, range_start, insert_before)
    return [added_ats[i] for i in items]


def expected(added_ats, order):
    return sorted(added_ats, key=lambda v: v or "", reverse=order != OLDEST_FIRST)


class ApplyMoveTest(unittest.TestCase):
    def test_move_last_to_front(self):
        # Spotify's own documented example: range_start=3, insert_before=0.
        self.assertEqual(apply_move(["a", "b", "c", "d"], 3, 0), ["d", "a", "b", "c"])

    def test_move_first_to_end(self):
        self.assertEqual(apply_move(["a", "b", "c", "d"], 0, 4), ["b", "c", "d", "a"])

    def test_downward_move_lands_before_the_original_index(self):
        # The subtle one: insert_before is evaluated before the removal, so the
        # item ends up at insert_before - 1 when moving down.
        self.assertEqual(apply_move(["a", "b", "c", "d"], 0, 3), ["b", "c", "a", "d"])


class PlanMovesTest(unittest.TestCase):
    def assert_sorts(self, added_ats, order=NEWEST_FIRST):
        moves = plan_moves(added_ats, order)
        self.assertEqual(replay(added_ats, moves), expected(added_ats, order))
        return moves

    def test_empty_and_single(self):
        self.assertEqual(plan_moves([]), [])
        self.assertEqual(plan_moves(["2024-01-01T00:00:00Z"]), [])

    def test_already_sorted_costs_nothing(self):
        dates = ["2024-03-01T00:00:00Z", "2024-02-01T00:00:00Z", "2024-01-01T00:00:00Z"]
        self.assertEqual(self.assert_sorts(dates), [])

    def test_reversed_playlist(self):
        dates = ["2024-0%d-01T00:00:00Z" % i for i in range(1, 8)]
        self.assert_sorts(dates)

    def test_oldest_first(self):
        dates = ["2024-03-01T00:00:00Z", "2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z"]
        self.assert_sorts(dates, OLDEST_FIRST)

    def test_three_new_tracks_appended_cost_three_moves(self):
        # The everyday case: sorted newest-first yesterday, three tracks added since.
        dates = ["2024-01-%02dT00:00:00Z" % d for d in range(20, 0, -1)]
        dates += ["2024-02-01T00:00:00Z", "2024-02-02T00:00:00Z", "2024-02-03T00:00:00Z"]
        moves = self.assert_sorts(dates)
        self.assertEqual(len(moves), 3)

    def test_one_track_from_the_end_belongs_at_the_front(self):
        dates = ["2024-01-%02dT00:00:00Z" % d for d in range(9, 0, -1)] + ["2024-05-01T00:00:00Z"]
        self.assertEqual(len(self.assert_sorts(dates)), 1)

    def test_duplicate_tracks_do_not_loop(self):
        # Two copies of the same track, same timestamp: the old id-based lookup
        # always resolved both to the first copy and recursed forever.
        dates = ["2024-03-01T00:00:00Z", "2024-01-01T00:00:00Z",
                 "2024-03-01T00:00:00Z", "2024-02-01T00:00:00Z"]
        self.assert_sorts(dates)

    def test_all_timestamps_identical(self):
        self.assertEqual(self.assert_sorts(["2024-01-01T00:00:00Z"] * 12), [])

    def test_null_added_at_is_treated_as_oldest(self):
        dates = [None, "2024-01-01T00:00:00Z", None, "2024-02-01T00:00:00Z"]
        self.assert_sorts(dates)

    def test_ties_keep_their_relative_order(self):
        dates = ["2024-01-01T00:00:00Z"] * 5 + ["2024-02-01T00:00:00Z"]
        moves = self.assert_sorts(dates)
        self.assertEqual(len(moves), 1)  # only the newer track has to move

    def test_moves_are_minimal(self):
        # n - len(LIS of target ranks) is the lower bound on single-item moves.
        for seed in range(200):
            rng = random.Random(seed)
            n = rng.randint(2, 40)
            dates = ["2024-01-01T00:00:%02dZ" % rng.randint(0, 30) for _ in range(n)]
            moves = plan_moves(dates, NEWEST_FIRST)
            target = sorted(range(n), key=lambda i: dates[i], reverse=True)
            rank = [0] * n
            for pos, orig in enumerate(target):
                rank[orig] = pos
            lis = _naive_lis(rank)
            self.assertLessEqual(len(moves), n - lis, "seed %d" % seed)

    def test_randomised(self):
        for seed in range(500):
            rng = random.Random(seed)
            n = rng.randint(0, 60)
            dates = [None if rng.random() < 0.05 else "2024-%02d-%02dT00:00:00Z" % (
                rng.randint(1, 12), rng.randint(1, 28)) for _ in range(n)]
            for order in (NEWEST_FIRST, OLDEST_FIRST):
                moves = plan_moves(dates, order)
                self.assertEqual(replay(dates, moves), expected(dates, order),
                                 "seed %d order %s" % (seed, order))
                self.assertLessEqual(len(moves), max(0, n - 1))

    def test_large_playlist_does_not_recurse(self):
        # The old implementation recursed once per move and blew the stack here.
        dates = ["2024-01-01T00:00:%02dZ" % (i % 60) for i in range(5000)]
        moves = plan_moves(dates, NEWEST_FIRST)
        self.assertEqual(replay(dates, moves), expected(dates, NEWEST_FIRST))


def _naive_lis(seq):
    if not seq:
        return 0
    best = [1] * len(seq)
    for i in range(len(seq)):
        for j in range(i):
            if seq[j] < seq[i]:
                best[i] = max(best[i], best[j] + 1)
    return max(best)


class ParsePlaylistIdsTest(unittest.TestCase):
    def test_formats(self):
        from spotisort.config import parse_playlist_ids
        raw = ("37i9dQZF1DX0XUsuxWHRQd,  37i9dQZF1DXcBWIGoYBM5M\n"
               "https://open.spotify.com/playlist/1AbcDefGhiJklMnoPqrStu?si=xyz\n"
               "spotify:playlist:2ZzZzZzZzZzZzZzZzZzZzZ\n"
               "37i9dQZF1DX0XUsuxWHRQd")  # duplicate, should collapse
        self.assertEqual(parse_playlist_ids(raw), [
            "37i9dQZF1DX0XUsuxWHRQd",
            "37i9dQZF1DXcBWIGoYBM5M",
            "1AbcDefGhiJklMnoPqrStu",
            "2ZzZzZzZzZzZzZzZzZzZzZ",
        ])

    def test_empty(self):
        from spotisort.config import parse_playlist_ids
        self.assertEqual(parse_playlist_ids("  , ,\n"), [])


class NormaliseEntriesTest(unittest.TestCase):
    def normalise(self, raw, default="newest_first"):
        from spotisort.config import normalise_entries
        return normalise_entries(raw, default)

    def test_bare_ids_migrate_to_the_default_order(self):
        # The pre-per-playlist-order config format.
        self.assertEqual(self.normalise(["aaa", "bbb"], "oldest_first"), [
            {"id": "aaa", "order": "oldest_first"},
            {"id": "bbb", "order": "oldest_first"},
        ])

    def test_each_entry_keeps_its_own_order(self):
        raw = [{"id": "aaa", "order": "oldest_first"}, {"id": "bbb", "order": "newest_first"}]
        self.assertEqual(self.normalise(raw, "newest_first"), raw)

    def test_unknown_order_falls_back_to_the_default(self):
        self.assertEqual(self.normalise([{"id": "aaa", "order": "sideways"}], "oldest_first"),
                         [{"id": "aaa", "order": "oldest_first"}])

    def test_mixed_formats_and_urls(self):
        raw = ["aaa", {"id": "https://open.spotify.com/playlist/bbb?si=1", "order": "oldest_first"}]
        self.assertEqual(self.normalise(raw), [
            {"id": "aaa", "order": "newest_first"},
            {"id": "bbb", "order": "oldest_first"},
        ])

    def test_first_occurrence_of_a_duplicate_wins(self):
        raw = [{"id": "aaa", "order": "oldest_first"}, {"id": "aaa", "order": "newest_first"}]
        self.assertEqual(self.normalise(raw), [{"id": "aaa", "order": "oldest_first"}])

    def test_junk_is_dropped(self):
        self.assertEqual(self.normalise([None, 42, {}, {"id": ""}, ""]), [])
        self.assertEqual(self.normalise(None), [])


class ConfigMigrationTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        for var in ("PLAYLIST_IDS", "SORT_ORDER", "INTERVAL_MINUTES"):
            os.environ.pop(var, None)

    def load(self):
        from spotisort.config import Config
        return Config(self.tmp)

    def write(self, payload):
        import json
        with open(os.path.join(self.tmp, "config.json"), "w") as fh:
            json.dump(payload, fh)

    def test_old_config_is_migrated_in_place(self):
        self.write({"playlists": ["aaa", "bbb"], "order": "oldest_first"})
        cfg = self.load()
        self.assertEqual(cfg.entries, [
            {"id": "aaa", "order": "oldest_first"},
            {"id": "bbb", "order": "oldest_first"},
        ])
        self.assertEqual(cfg.playlists, ["aaa", "bbb"])

    def test_per_playlist_orders_survive_a_reload(self):
        cfg = self.load()
        cfg.set_entries([{"id": "aaa", "order": "oldest_first"}, "bbb"])
        self.assertEqual(self.load().entries, [
            {"id": "aaa", "order": "oldest_first"},
            {"id": "bbb", "order": "newest_first"},
        ])

    def test_changing_the_default_leaves_existing_playlists_alone(self):
        cfg = self.load()
        cfg.set_entries(["aaa"])
        cfg.update(order="oldest_first")
        cfg.set_entries(cfg.entries + ["bbb"])
        self.assertEqual(cfg.entries, [
            {"id": "aaa", "order": "newest_first"},
            {"id": "bbb", "order": "oldest_first"},
        ])

    def test_playlist_ids_env_seeds_with_the_default_order(self):
        os.environ["PLAYLIST_IDS"] = "aaa bbb"
        os.environ["SORT_ORDER"] = "oldest_first"
        try:
            self.assertEqual(self.load().entries, [
                {"id": "aaa", "order": "oldest_first"},
                {"id": "bbb", "order": "oldest_first"},
            ])
        finally:
            del os.environ["PLAYLIST_IDS"], os.environ["SORT_ORDER"]


if __name__ == "__main__":
    unittest.main()
