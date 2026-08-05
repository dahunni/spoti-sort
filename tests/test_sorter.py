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


def pairs(entries):
    """(id, order) view of entries, so tests don't break when fields are added."""
    return [(e["id"], e["order"]) for e in entries]


class NormaliseEntriesTest(unittest.TestCase):
    def normalise(self, raw, default="newest_first"):
        from spotisort.config import normalise_entries
        return normalise_entries(raw, default)

    def test_bare_ids_migrate_to_the_default_order(self):
        # The pre-per-playlist-order config format.
        self.assertEqual(pairs(self.normalise(["aaa", "bbb"], "oldest_first")),
                         [("aaa", "oldest_first"), ("bbb", "oldest_first")])

    def test_each_entry_keeps_its_own_order(self):
        raw = [{"id": "aaa", "order": "oldest_first"}, {"id": "bbb", "order": "newest_first"}]
        self.assertEqual(pairs(self.normalise(raw, "newest_first")),
                         [("aaa", "oldest_first"), ("bbb", "newest_first")])

    def test_unknown_order_falls_back_to_the_default(self):
        self.assertEqual(pairs(self.normalise([{"id": "aaa", "order": "sideways"}], "oldest_first")),
                         [("aaa", "oldest_first")])

    def test_mixed_formats_and_urls(self):
        raw = ["aaa", {"id": "https://open.spotify.com/playlist/bbb?si=1", "order": "oldest_first"}]
        self.assertEqual(pairs(self.normalise(raw)),
                         [("aaa", "newest_first"), ("bbb", "oldest_first")])

    def test_first_occurrence_of_a_duplicate_wins(self):
        raw = [{"id": "aaa", "order": "oldest_first"}, {"id": "aaa", "order": "newest_first"}]
        self.assertEqual(pairs(self.normalise(raw)), [("aaa", "oldest_first")])

    def test_junk_is_dropped(self):
        self.assertEqual(self.normalise([None, 42, {}, {"id": ""}, ""]), [])
        self.assertEqual(self.normalise(None), [])


class PublicUrlTest(unittest.TestCase):
    def clean(self, raw):
        from spotisort.config import clean_public_url
        return clean_public_url(raw)

    def test_trailing_slash_is_stripped(self):
        # Spotify compares redirect URIs byte for byte, so this matters.
        self.assertEqual(self.clean("http://192.168.1.50:8080/"), "http://192.168.1.50:8080")

    def test_scheme_is_added_when_missing(self):
        self.assertEqual(self.clean("192.168.1.50:8080"), "http://192.168.1.50:8080")

    def test_https_and_hostname(self):
        self.assertEqual(self.clean(" https://spoti.example.com "), "https://spoti.example.com")

    def test_subpath_is_kept_for_reverse_proxies(self):
        self.assertEqual(self.clean("https://home.example.com/spotisort/"),
                         "https://home.example.com/spotisort")

    def test_empty_means_default(self):
        self.assertEqual(self.clean("   "), "")

    def test_rejects_bad_input(self):
        for bad in ("ftp://host", "http://", "http://host?x=1", "http://host#f"):
            with self.assertRaises(ValueError, msg=bad):
                self.clean(bad)


class TeslaTokenTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        for var in ("PLAYLIST_IDS", "SORT_ORDER", "INTERVAL_MINUTES", "PUBLIC_URL",
                    "REDIRECT_URI", "PORT"):
            os.environ.pop(var, None)

    def load(self):
        from spotisort.config import Config
        return Config(self.tmp)

    def test_disabled_by_default(self):
        cfg = self.load()
        self.assertEqual(cfg.tesla_token, "")
        self.assertEqual(cfg.tesla_url, "")

    def test_token_survives_a_restart_and_builds_a_url(self):
        cfg = self.load()
        cfg.update(public_url="http://192.168.1.50:8080")
        token = cfg.new_tesla_token()
        self.assertGreaterEqual(len(token), 20)
        self.assertEqual(self.load().tesla_url, "http://192.168.1.50:8080/tesla/" + token)

    def test_regenerating_changes_the_token(self):
        cfg = self.load()
        first = cfg.new_tesla_token()
        self.assertNotEqual(cfg.new_tesla_token(), first)

    def test_disable_clears_the_url(self):
        cfg = self.load()
        cfg.new_tesla_token()
        cfg.clear_tesla_token()
        self.assertEqual(cfg.tesla_url, "")

    def test_https_public_url_drives_the_redirect_uri(self):
        cfg = self.load()
        cfg.update(public_url="https://spoti.example.com")
        self.assertEqual(cfg.redirect_uri, "https://spoti.example.com/callback")
        self.assertFalse(cfg.redirect_uri_is_loopback)

    def test_http_lan_address_falls_back_to_loopback(self):
        # Spotify rejects plain http to anything but a loopback literal with
        # "Insecure redirect URI", so the LAN address must not be used here.
        cfg = self.load()
        cfg.update(public_url="http://192.168.1.50:8080")
        self.assertEqual(cfg.redirect_uri, "http://127.0.0.1:8080/callback")
        self.assertTrue(cfg.redirect_uri_is_loopback)

    def test_http_hostname_also_falls_back(self):
        cfg = self.load()
        cfg.update(public_url="http://spoti.example.com")
        self.assertEqual(cfg.redirect_uri, "http://127.0.0.1:8080/callback")

    def test_the_public_address_is_still_used_for_the_tesla_link(self):
        # Only the redirect URI is constrained; the car page is a plain web page.
        cfg = self.load()
        cfg.update(public_url="http://192.168.1.50:8080")
        cfg.new_tesla_token()
        self.assertTrue(cfg.tesla_url.startswith("http://192.168.1.50:8080/tesla/"))

    def test_redirect_uri_env_overrides_public_url(self):
        cfg = self.load()
        cfg.update(public_url="https://spoti.example.com")
        os.environ["REDIRECT_URI"] = "https://other.example.com/callback"
        try:
            self.assertEqual(cfg.redirect_uri, "https://other.example.com/callback")
            # ...but the Tesla link still follows the public address.
            self.assertTrue(cfg.base_url.startswith("https://spoti.example.com"))
        finally:
            del os.environ["REDIRECT_URI"]

    def test_default_redirect_uri_is_loopback(self):
        self.assertEqual(self.load().redirect_uri, "http://127.0.0.1:8080/callback")

    def test_url_parts_split_for_the_setup_form(self):
        cfg = self.load()
        self.assertEqual(cfg.public_url_parts, {"scheme": "http", "host": "127.0.0.1:8080"})
        cfg.update(public_url="https://spoti.example.com")
        self.assertEqual(cfg.public_url_parts, {"scheme": "https", "host": "spoti.example.com"})

    def test_url_parts_keep_a_reverse_proxy_subpath(self):
        cfg = self.load()
        cfg.update(public_url="https://example.com/spotisort")
        self.assertEqual(cfg.public_url_parts,
                         {"scheme": "https", "host": "example.com/spotisort"})

    def test_parts_round_trip_through_clean_public_url(self):
        from spotisort.config import clean_public_url
        cfg = self.load()
        for url in ("https://spoti.example.com", "http://192.168.1.50:8080",
                    "https://example.com/spotisort"):
            cfg.update(public_url=url)
            parts = cfg.public_url_parts
            # What the form composes must normalise back to what was stored.
            self.assertEqual(clean_public_url("%s://%s" % (parts["scheme"], parts["host"])), url)


class ScopeTest(unittest.TestCase):
    def test_old_token_reports_the_new_playback_scopes(self):
        from spotisort.spotify import missing_scopes
        old = {"scope": ("playlist-read-private playlist-read-collaborative "
                         "playlist-modify-private playlist-modify-public")}
        self.assertEqual(sorted(missing_scopes(old)),
                         ["user-read-currently-playing", "user-read-playback-state"])

    def test_current_token_is_complete(self):
        from spotisort.spotify import SCOPE, missing_scopes
        self.assertEqual(missing_scopes({"scope": SCOPE}), [])

    def test_comma_separated_scopes_are_understood(self):
        from spotisort.spotify import SCOPE, missing_scopes
        self.assertEqual(missing_scopes({"scope": SCOPE.replace(" ", ",")}), [])

    def test_absent_token_needs_everything(self):
        from spotisort.spotify import SCOPE, missing_scopes
        self.assertEqual(missing_scopes(None), SCOPE.split())


class IndependentRolesTest(unittest.TestCase):
    """Sorting and Tesla quick-add are separate opt-ins per playlist."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        for var in ("PLAYLIST_IDS", "SORT_ORDER", "INTERVAL_MINUTES", "PUBLIC_URL"):
            os.environ.pop(var, None)

    def load(self):
        from spotisort.config import Config
        return Config(self.tmp)

    def ids(self, entries):
        return [e["id"] for e in entries]

    def test_config_without_the_flags_keeps_doing_both(self):
        # Anything written before the split was sorted *and* offered on the car page.
        import json
        with open(os.path.join(self.tmp, "config.json"), "w") as fh:
            json.dump({"playlists": [{"id": "aaa", "order": "newest_first"}]}, fh)
        cfg = self.load()
        self.assertEqual(self.ids(cfg.sort_entries), ["aaa"])
        self.assertEqual(self.ids(cfg.add_entries), ["aaa"])

    def test_bare_ids_also_default_to_both(self):
        cfg = self.load()
        cfg.set_entries(["aaa"])
        self.assertEqual(self.ids(cfg.sort_entries), ["aaa"])
        self.assertEqual(self.ids(cfg.add_entries), ["aaa"])

    def test_sort_only(self):
        cfg = self.load()
        cfg.set_entries([{"id": "aaa", "sort": True, "add": False}])
        self.assertEqual(self.ids(cfg.sort_entries), ["aaa"])
        self.assertEqual(self.ids(cfg.add_entries), [])

    def test_add_only_is_never_reordered(self):
        # The point of the split: a playlist you only ever throw tracks into.
        cfg = self.load()
        cfg.set_entries([{"id": "aaa", "sort": False, "add": True}])
        self.assertEqual(self.ids(cfg.sort_entries), [])
        self.assertEqual(self.ids(cfg.add_entries), ["aaa"])

    def test_the_two_sets_can_be_disjoint(self):
        cfg = self.load()
        cfg.set_entries([
            {"id": "sorted-only", "sort": True, "add": False},
            {"id": "car-only", "sort": False, "add": True},
            {"id": "both", "sort": True, "add": True},
        ])
        self.assertEqual(self.ids(cfg.sort_entries), ["sorted-only", "both"])
        self.assertEqual(self.ids(cfg.add_entries), ["car-only", "both"])

    def test_neither_role_means_deselected(self):
        cfg = self.load()
        cfg.set_entries([{"id": "aaa", "sort": False, "add": False}])
        self.assertEqual(cfg.entries, [])

    def test_roles_survive_a_restart(self):
        cfg = self.load()
        cfg.set_entries([{"id": "aaa", "sort": False, "add": True}])
        reloaded = self.load()
        self.assertEqual(self.ids(reloaded.sort_entries), [])
        self.assertEqual(self.ids(reloaded.add_entries), ["aaa"])

    def test_favourite_and_recency_still_work_on_an_add_only_playlist(self):
        cfg = self.load()
        cfg.set_entries([{"id": "aaa", "sort": False, "add": True}])
        self.assertTrue(cfg.set_favorite("aaa", True))
        cfg.touch_entry("aaa")
        entry = self.load().add_entries[0]
        self.assertTrue(entry["favorite"])
        self.assertGreater(entry["last_used"], 0)


class AddPositionTest(unittest.TestCase):
    """A track added from the car must land where the playlist's order says."""

    class FakeSp:
        def __init__(self, total):
            self.total = total
            self.calls = []

        def _get(self, path, **kw):
            self.calls.append(("GET", path, kw))
            return {"total": self.total}

        def _post(self, path, payload=None):
            self.calls.append(("POST", path, payload))
            return {"snapshot_id": "after-add"}

        def _put(self, path, payload=None):
            self.calls.append(("PUT", path, payload))
            return {"snapshot_id": "after-move"}

    def client(self, total):
        from spotisort.spotify import SpotifyClient
        c = SpotifyClient.__new__(SpotifyClient)
        c.sp = self.FakeSp(total)
        c._me = None
        return c

    def test_newest_first_moves_the_new_track_to_the_front(self):
        c = self.client(12)
        info = c.add_to_playlist("pl", "spotify:track:x", "newest_first")
        verbs = [v for v, _, _ in c.sp.calls]
        self.assertEqual(verbs, ["GET", "POST", "PUT"])
        move = c.sp.calls[2][2]
        self.assertEqual(move["range_start"], 12)   # appended at the end...
        self.assertEqual(move["insert_before"], 0)  # ...then moved to the front
        self.assertEqual(move["snapshot_id"], "after-add")
        # Undo has to target where it actually ended up, not where it landed first.
        self.assertEqual(info["position"], 0)
        self.assertEqual(info["snapshot"], "after-move")

    def test_oldest_first_leaves_it_appended(self):
        c = self.client(12)
        info = c.add_to_playlist("pl", "spotify:track:x", "oldest_first")
        self.assertEqual([v for v, _, _ in c.sp.calls], ["GET", "POST"])
        self.assertEqual(info["position"], 12)
        self.assertEqual(info["snapshot"], "after-add")

    def test_empty_playlist_needs_no_move(self):
        c = self.client(0)
        info = c.add_to_playlist("pl", "spotify:track:x", "newest_first")
        self.assertEqual([v for v, _, _ in c.sp.calls], ["GET", "POST"])
        self.assertEqual(info["position"], 0)

    def test_uses_the_new_items_endpoint(self):
        c = self.client(3)
        c.add_to_playlist("pl", "spotify:track:x", "newest_first")
        for _, path, _ in c.sp.calls:
            self.assertEqual(path, "playlists/pl/items")


class ItemUriTest(unittest.TestCase):
    def test_reads_the_migrated_item_key(self):
        from spotisort.sorter import item_uri
        self.assertEqual(item_uri({"item": {"uri": "spotify:track:x"}}), "spotify:track:x")

    def test_missing_or_local_entries_have_no_uri(self):
        from spotisort.sorter import item_uri
        for entry in ({}, {"item": None}, {"item": {}}, None):
            self.assertIsNone(item_uri(entry))


class TeslaOnboardingTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        for var in ("PUBLIC_URL", "UI_PASSWORD"):
            os.environ.pop(var, None)

    def load(self):
        from spotisort.config import Config
        return Config(self.tmp)

    def test_starts_not_onboarded_and_persists_once_dismissed(self):
        cfg = self.load()
        cfg.new_tesla_token()
        self.assertFalse(cfg.tesla_onboarded)
        cfg.mark_tesla_onboarded()
        # Server-side: the car loses local storage, so this must survive a restart.
        self.assertTrue(self.load().tesla_onboarded)

    def test_regenerating_asks_again(self):
        cfg = self.load()
        cfg.new_tesla_token()
        cfg.mark_tesla_onboarded()
        cfg.new_tesla_token()
        self.assertFalse(cfg.tesla_onboarded)


class PasswordTest(unittest.TestCase):
    def test_round_trip(self):
        from spotisort.security import hash_password, verify_password
        stored = hash_password("correct horse battery")
        self.assertTrue(verify_password("correct horse battery", stored))
        self.assertFalse(verify_password("wrong", stored))

    def test_hash_is_salted(self):
        from spotisort.security import hash_password
        self.assertNotEqual(hash_password("same"), hash_password("same"))

    def test_plaintext_is_not_stored(self):
        from spotisort.security import hash_password
        self.assertNotIn("hunter2", hash_password("hunter2"))

    def test_garbage_never_verifies(self):
        from spotisort.security import verify_password
        for stored in ("", "nonsense", "md5$1$aa$bb", "pbkdf2_sha256$x$y$z"):
            self.assertFalse(verify_password("anything", stored), stored)

    def test_empty_password_never_verifies(self):
        from spotisort.security import hash_password, verify_password
        self.assertFalse(verify_password("", hash_password("x")))


class LoginLimiterTest(unittest.TestCase):
    def test_lockout_kicks_in_after_repeated_failures(self):
        from spotisort.security import MAX_FAILURES, LoginLimiter
        limiter = LoginLimiter()
        for _ in range(MAX_FAILURES - 1):
            self.assertEqual(limiter.record_failure("1.2.3.4"), 0.0)
        self.assertGreater(limiter.record_failure("1.2.3.4"), 0)
        self.assertGreater(limiter.retry_after("1.2.3.4"), 0)

    def test_lockout_grows(self):
        from spotisort.security import MAX_FAILURES, LoginLimiter
        limiter = LoginLimiter()
        delays = [limiter.record_failure("x") for _ in range(MAX_FAILURES + 3)]
        self.assertGreater(delays[-1], delays[MAX_FAILURES - 1])

    def test_success_clears_and_addresses_are_independent(self):
        from spotisort.security import MAX_FAILURES, LoginLimiter
        limiter = LoginLimiter()
        for _ in range(MAX_FAILURES):
            limiter.record_failure("bad")
        self.assertEqual(limiter.retry_after("other"), 0.0)
        limiter.record_success("bad")
        self.assertEqual(limiter.retry_after("bad"), 0.0)


class FavoritesAndRecencyTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        for var in ("PLAYLIST_IDS", "SORT_ORDER", "INTERVAL_MINUTES", "PUBLIC_URL"):
            os.environ.pop(var, None)

    def load(self):
        from spotisort.config import Config
        return Config(self.tmp)

    def test_defaults(self):
        cfg = self.load()
        cfg.set_entries(["aaa"])
        self.assertEqual(cfg.entries[0]["favorite"], False)
        self.assertEqual(cfg.entries[0]["last_used"], 0.0)

    def test_favorite_and_recency_persist(self):
        cfg = self.load()
        cfg.set_entries(["aaa", "bbb"])
        cfg.set_favorite("aaa", True)
        cfg.touch_entry("bbb")
        reloaded = self.load().entries
        self.assertTrue(reloaded[0]["favorite"])
        self.assertGreater(reloaded[1]["last_used"], 0)

    def test_saving_a_selection_keeps_usage_history(self):
        # The UI re-sends the selection on every save and knows nothing about
        # last_used; it must not be wiped.
        cfg = self.load()
        cfg.set_entries(["aaa"])
        cfg.touch_entry("aaa")
        used = cfg.entries[0]["last_used"]
        cfg.set_entries([{"id": "aaa", "order": "oldest_first"}])
        self.assertEqual(cfg.entries[0]["last_used"], used)

    def test_saving_bare_ids_keeps_favourites(self):
        cfg = self.load()
        cfg.set_entries(["aaa"])
        cfg.set_favorite("aaa", True)
        cfg.set_entries(["aaa", "bbb"])
        self.assertTrue(cfg.entries[0]["favorite"])

    def test_explicit_favorite_false_clears_it(self):
        cfg = self.load()
        cfg.set_entries(["aaa"])
        cfg.set_favorite("aaa", True)
        cfg.set_entries([{"id": "aaa", "favorite": False}])
        self.assertFalse(cfg.entries[0]["favorite"])

    def test_favorite_on_unknown_playlist_reports_failure(self):
        cfg = self.load()
        self.assertFalse(cfg.set_favorite("nope", True))

    def test_is_public(self):
        cfg = self.load()
        self.assertFalse(cfg.is_public)
        cfg.update(public_url="http://192.168.1.50:8080")
        self.assertTrue(cfg.is_public)


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
        self.assertEqual(pairs(cfg.entries),
                         [("aaa", "oldest_first"), ("bbb", "oldest_first")])
        self.assertEqual(cfg.playlists, ["aaa", "bbb"])

    def test_per_playlist_orders_survive_a_reload(self):
        cfg = self.load()
        cfg.set_entries([{"id": "aaa", "order": "oldest_first"}, "bbb"])
        self.assertEqual(pairs(self.load().entries),
                         [("aaa", "oldest_first"), ("bbb", "newest_first")])

    def test_changing_the_default_leaves_existing_playlists_alone(self):
        cfg = self.load()
        cfg.set_entries(["aaa"])
        cfg.update(order="oldest_first")
        cfg.set_entries(cfg.entries + ["bbb"])
        self.assertEqual(pairs(cfg.entries),
                         [("aaa", "newest_first"), ("bbb", "oldest_first")])

    def test_playlist_ids_env_seeds_with_the_default_order(self):
        os.environ["PLAYLIST_IDS"] = "aaa bbb"
        os.environ["SORT_ORDER"] = "oldest_first"
        try:
            self.assertEqual(pairs(self.load().entries),
                             [("aaa", "oldest_first"), ("bbb", "oldest_first")])
        finally:
            del os.environ["PLAYLIST_IDS"], os.environ["SORT_ORDER"]


if __name__ == "__main__":
    unittest.main()


class VersionTest(unittest.TestCase):
    def test_is_semver_on_the_two_line(self):
        from spotisort import __version__
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3, __version__)
        self.assertTrue(all(p.isdigit() for p in parts), __version__)
        self.assertEqual(parts[0], "2", "the rewritten generation is 2.x")

    def test_is_surfaced_to_the_ui(self):
        import tempfile
        from spotisort import __version__
        from spotisort.config import Config
        from spotisort.web import create_app
        for var in ("UI_PASSWORD", "PUBLIC_URL"):
            os.environ.pop(var, None)
        app = create_app(Config(tempfile.mkdtemp()))
        client = app.test_client()
        self.assertIn("v" + __version__, client.get("/").get_data(as_text=True))
        self.assertEqual(client.get("/api/status").get_json()["version"], __version__)
