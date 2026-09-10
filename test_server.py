import json
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from unittest.mock import patch

import server


def leaguepedia_row(blue, red, blue_score=None, red_score=None):
    scores = ""
    if blue_score is not None:
        scores = f'<span class="matchlist-score">{blue_score}</span>'
    red_scores = ""
    if red_score is not None:
        red_scores = f'<span class="matchlist-score">{red_score}</span>'
    return f"""
      <tr class="ml-row" data-date="2026-08-30">
        <td class="matchlist-team1"><span class="teamname">{blue}</span>{scores}</td>
        <td class="matchlist-team2"><span class="teamname">{red}</span>{red_scores}</td>
        <span class="teamname">Not a participant</span>
        <span class="countdowndate">30 August 2026 12:00:00 +0800</span>
      </tr>
    """


def official_fragment(match_id, blue="T1", red="GEN"):
    return {
        "__typename": "EventMatch",
        "id": str(match_id),
        "startTime": "2026-08-30T04:00:00Z",
        "tournamentName": "LCK",
        "teams": [
            {"__typename": "MatchTeam", "id": "team:1", "name": blue, "code": blue},
            {"__typename": "MatchTeam", "id": "team:2", "name": red, "code": red},
        ],
        "games": [
            {"__typename": "Game", "id": "1001", "state": "completed", "number": 1}
        ],
    }


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        server.CACHE.update({
            "expires": server.datetime.min.replace(tzinfo=server.timezone.utc),
            "matches": [],
            "stage": {"page": "", "label": "", "year": "", "key": "", "refreshedAt": ""},
            "stale": False,
            "error": None,
        })
        server.SCHEDULE_CACHE.update({
            "expires": server.datetime.min.replace(tzinfo=server.timezone.utc),
            "matches": [],
            "stale": False,
            "error": None,
        })
        server.STANDINGS_CACHE.update({
            "expires": server.datetime.min.replace(tzinfo=server.timezone.utc),
            "rows": [],
            "competition": {"league": "LCK", "label": "LCK", "stage": ""},
            "stale": False,
            "error": None,
        })
        server.OFFICIAL_CACHE["expires"] = server.datetime.min.replace(
            tzinfo=server.timezone.utc
        )
        server.OFFICIAL_CACHE["by_key"] = {}
        server.OFFICIAL_CACHE["by_id"] = {}
        server.OFFICIAL_CACHE["diagnostics"] = {}
        server.LOGO_CACHE.clear()
        server.DETAIL_CACHE.clear()

    def test_team_aliases_are_canonical(self):
        self.assertEqual(server.team_code("Gen.G"), "GEN")
        self.assertEqual(server.team_code("FearX"), "BFX")
        self.assertEqual(server.team_code("Hanwha Life Esports"), "HLE")

    def test_run_with_timeout_raises_timeout_error(self):
        blocked = Event()

        def slow():
            blocked.wait(timeout=0.2)

        with self.assertRaises(TimeoutError):
            server.run_with_timeout(slow, timeout_seconds=0.01)
        blocked.set()

    def test_logo_host_allowlist_accepts_supported_hosts(self):
        self.assertTrue(server.is_allowed_logo_host(
            "https://static.wikia.nocookie.net/example.png"
        ))
        self.assertTrue(server.is_allowed_logo_host(
            "https://vignette.wikia.nocookie.net/example.png"
        ))
        self.assertTrue(server.is_allowed_logo_host(
            "https://lol.fandom.com/wiki/T1"
        ))
        self.assertTrue(server.is_allowed_logo_host(
            "https://static.fandom.com/wiki/T1"
        ))
        self.assertFalse(server.is_allowed_logo_host(
            "http://static.wikia.nocookie.net/example.png"
        ))
        self.assertFalse(server.is_allowed_logo_host(
            "https://example.com/wiki/T1"
        ))

    def test_parse_matches_uses_the_two_team_cells(self):
        parsed = server.parse_matches("LCK", "LCK/2026_Season/Stage", leaguepedia_row(
            "T1", "Gen.G", 2, 1
        ))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(
            (parsed[0]["blue"], parsed[0]["red"], parsed[0]["blueScore"], parsed[0]["redScore"]),
            ("T1", "Gen.G", 2, 1),
        )

    def test_parse_matches_normalizes_combined_bo5_score(self):
        html = """
          <tr class="ml-row" data-date="2026-08-30">
            <td class="matchlist-team1"><span class="teamname">T1</span></td>
            <td class="matchlist-team2"><span class="teamname">HLE</span></td>
            <td class="matchlist-score">2–3</td>
            <span class="countdowndate">30 August 2026 12:00:00 +0800</span>
          </tr>
        """
        parsed = server.parse_matches("LCK", "LCK/2026_Season/Stage", html)
        self.assertEqual(
            (parsed[0]["blueScore"], parsed[0]["redScore"], parsed[0]["series"], parsed[0]["status"]),
            (2, 3, "BO5", "completed"),
        )

    def test_official_index_deduplicates_ids_and_supports_escaped_json(self):
        fragment = json.dumps(official_fragment("42"))
        escaped = fragment.replace('"', '\\"')
        with patch.object(server, "fetch_official_schedule", return_value=escaped):
            server.OFFICIAL_CACHE["expires"] = server.datetime.min.replace(
                tzinfo=server.timezone.utc
            )
            index = server.load_official_index()
        self.assertEqual(list(index["by_id"]), ["42"])
        self.assertEqual(
            server.find_official_match(index, "2026-08-30", ("GEN", "T1"))["matchId"],
            "42",
        )

    def test_concurrent_official_index_loads_share_one_upstream_request(self):
        fragment = json.dumps(official_fragment("42"))
        started = Event()
        release = Event()
        calls = []

        def slow_schedule():
            calls.append(True)
            started.set()
            release.wait(timeout=1)
            return fragment

        server.OFFICIAL_CACHE["expires"] = server.datetime.min.replace(
            tzinfo=server.timezone.utc
        )
        with patch.object(server, "fetch_official_schedule", side_effect=slow_schedule):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(server.load_official_index)
                    for _ in range(2)
                ]
                self.assertTrue(started.wait(timeout=1))
                release.set()
                indexes = [future.result() for future in futures]
        self.assertEqual(len(calls), 1)
        self.assertEqual(indexes[0]["by_id"], indexes[1]["by_id"])

    def test_official_index_links_multiple_matches_with_aliases(self):
        first = official_fragment("event-gen")
        second = official_fragment("event-hle", "T1", "Hanwha Life Esports")
        second["startTime"] = "2026-08-30T08:00:00Z"
        payload = json.dumps(first) + "\n" + json.dumps(second)
        with patch.object(server, "fetch_official_schedule", return_value=payload):
            server.OFFICIAL_CACHE["expires"] = server.datetime.min.replace(
                tzinfo=server.timezone.utc
            )
            index = server.load_official_index()
        self.assertEqual(set(index["by_id"]), {"event-gen", "event-hle"})
        self.assertEqual(
            server.find_official_match(index, "2026-08-30", ("T1", "HLE"), "16:00")["matchId"],
            "event-hle",
        )

    def test_official_index_tolerates_key_order_and_skips_bad_event(self):
        valid = official_fragment("valid")
        reordered = {
            "id": valid["id"],
            "startTime": valid["startTime"],
            "__typename": valid["__typename"],
            "teams": valid["teams"],
            "games": valid["games"],
        }
        malformed = {
            "__typename": "EventMatch",
            "id": "bad",
            "startTime": "not-a-date",
            "teams": valid["teams"],
        }
        with patch.object(
            server, "fetch_official_schedule",
            return_value=json.dumps(malformed) + json.dumps(reordered),
        ):
            server.OFFICIAL_CACHE["expires"] = server.datetime.min.replace(
                tzinfo=server.timezone.utc
            )
            index = server.load_official_index()
        self.assertEqual(set(index["by_id"]), {"valid"})
        self.assertEqual(index["diagnostics"]["skipped"], 1)

    def test_official_index_reads_nested_serialized_event_matches(self):
        event = {
            "__typename": "EventMatch",
            "id": "event-42",
            "startTime": "2026-08-30T04:00:00Z",
            "league": {"name": "LCK"},
            "match": {
                "id": "42",
                "teams": {
                    "blue": {"team": {"id": "team:1", "code": "T1"}},
                    "red": {"team": {"id": "team:2", "name": "Gen.G"}},
                },
                "games": {
                    "one": {"game": {
                        "gameId": "1001", "sequenceNumber": "1",
                        "status": "completed",
                        "gameStartTime": "2026-08-30T04:20:00Z",
                    }},
                },
            },
        }
        payload = json.dumps({"props": {"serialized": json.dumps({"events": [event]})}})
        with patch.object(server, "fetch_official_schedule", return_value=payload):
            server.OFFICIAL_CACHE["expires"] = server.datetime.min.replace(
                tzinfo=server.timezone.utc
            )
            index = server.load_official_index()
        entry = index["by_id"]["42"]
        self.assertEqual(entry["competition"], "LCK")
        self.assertEqual(entry["teamIds"]["team:2"], "GEN")
        self.assertEqual(entry["gameIds"], [{
            "id": "1001", "number": 1, "state": "completed",
            "startTime": "2026-08-30T04:20:00Z",
        }])

    def test_parse_matches_links_official_game_ids(self):
        official = official_fragment("42")
        index = {
            "by_key": {
                ("2026-08-30", ("GEN", "T1")): [{
                    "matchId": "42",
                    "startTime": "2026-08-30T04:00:00+00:00",
                    "teamIds": {"team:1": "T1", "team:2": "GEN"},
                    "gameIds": official["games"],
                }],
            },
            "by_date": {},
        }
        parsed = server.parse_matches(
            "LCK", "LCK/2026_Season/Stage",
            leaguepedia_row("T1", "Gen.G", 2, 1), index,
        )
        self.assertEqual(parsed[0]["matchId"], "42")
        self.assertEqual(parsed[0]["gameIds"][0]["id"], "1001")
        self.assertEqual(parsed[0]["officialLinkStatus"], "linked")

    def test_unresolved_opponent_cannot_have_a_completed_placeholder_score(self):
        parsed = server.parse_matches(
            "LCK", "LCK/2026_Season/Stage",
            leaguepedia_row("T1", "TBD", 8, 0),
        )
        self.assertEqual(
            (parsed[0]["blueScore"], parsed[0]["redScore"], parsed[0]["status"]),
            (None, None, "upcoming"),
        )
        self.assertTrue(server.is_unresolved_opponent("Winner of semifinal 1"))

    def test_unresolved_opponent_is_reconciled_with_unique_official_event(self):
        official = official_fragment("sep-6")
        official["startTime"] = "2026-09-06T04:00:00Z"
        official["teams"][1]["name"] = "Gen.G"
        index = {
            "by_date": {
                "2026-09-06": [server.normalize_official_event(official)],
            },
        }
        row = leaguepedia_row("T1", "TBD", 8, 0).replace(
            "2026-08-30", "2026-09-06"
        ).replace(
            "30 August 2026", "6 September 2026"
        )
        parsed = server.parse_matches("T1", "T1/Match_History", row, index)[0]
        self.assertEqual(
            (parsed["red"], parsed["redCode"], parsed["matchId"]),
            ("Gen.G", "GEN", "sep-6"),
        )
        self.assertEqual(parsed["gameIds"][0]["id"], "1001")
        self.assertEqual(
            (parsed["blueScore"], parsed["redScore"], parsed["status"]),
            (None, None, "upcoming"),
        )

    def test_unresolved_opponent_stays_unlinked_when_official_event_is_ambiguous(self):
        first = server.normalize_official_event(official_fragment("sep-6-gen"))
        second_event = official_fragment("sep-6-hle", "T1", "HLE")
        second = server.normalize_official_event(second_event)
        for event in (first, second):
            event["startTime"] = "2026-09-06T04:00:00+00:00"
        index = {"by_date": {"2026-09-06": [first, second]}}
        row = leaguepedia_row("T1", "TBD").replace(
            "2026-08-30", "2026-09-06"
        ).replace(
            "30 August 2026", "6 September 2026"
        )
        parsed = server.parse_matches("T1", "T1/Match_History", row, index)[0]
        self.assertEqual((parsed["red"], parsed.get("matchId")), ("TBD", None))

    def test_ambiguous_same_day_pair_is_not_linked(self):
        first = official_fragment("42")
        second = official_fragment("43")
        first["startTime"] = second["startTime"] = "2026-08-30T04:00:00Z"
        entries = []
        for item in (first, second):
            entries.append({
                "matchId": item["id"],
                "teamIds": {"team:1": "T1", "team:2": "GEN"},
            })
        index = {
            "by_key": {("2026-08-30", ("GEN", "T1")): entries},
            "by_date": {"2026-08-30": entries},
        }
        self.assertIsNone(server.find_official_match(index, "2026-08-30", ("T1", "GEN")))

    def test_ambiguous_same_day_pair_uses_local_start_time(self):
        first = {
            "matchId": "42",
            "startTime": "2026-08-30T04:00:00+00:00",
            "teamIds": {"team:1": "T1", "team:2": "GEN"},
        }
        second = {
            "matchId": "43",
            "startTime": "2026-08-30T08:00:00+00:00",
            "teamIds": {"team:1": "T1", "team:2": "GEN"},
        }
        index = {
            "by_key": {
                ("2026-08-30", ("GEN", "T1")): [first, second],
            },
            "by_date": {"2026-08-30": [first, second]},
        }
        self.assertEqual(
            server.find_official_match(index, "2026-08-30", ("T1", "GEN"), "16:00")["matchId"],
            "43",
        )

    def test_close_start_time_links_only_the_unique_nearest_match(self):
        first = {
            "matchId": "42", "startTime": "2026-08-30T04:05:00+00:00",
            "teamIds": {"team:1": "T1", "team:2": "GEN"},
        }
        second = {
            "matchId": "43", "startTime": "2026-08-30T07:00:00+00:00",
            "teamIds": {"team:1": "T1", "team:2": "GEN"},
        }
        index = {
            "by_key": {("2026-08-30", ("GEN", "T1")): [first, second]},
            "by_date": {"2026-08-30": [first, second]},
        }
        self.assertEqual(
            server.find_official_match(index, "2026-08-30", ("T1", "GEN"), "12:00")["matchId"],
            "42",
        )

    def test_duplicate_sources_merge_but_rematches_do_not(self):
        base = {
            "date": "2026-08-30", "time": "12:00", "league": "LCK",
            "competition": "LCK", "blue": "T1", "red": "Gen.G",
            "blueCode": "T1", "redCode": "GEN", "blueScore": 2, "redScore": 1,
            "status": "completed",
        }
        linked = {**base, "matchId": "42", "gameIds": [{"id": "game-1"}]}
        rematch = {**base, "time": "16:00", "matchId": "43"}
        merged = server.merge_match_records([base, linked, rematch])
        self.assertEqual(len(merged), 2)
        self.assertEqual({match.get("matchId") for match in merged}, {"42", "43"})

    def test_duplicate_sources_with_different_competitions_merge(self):
        base = {
            "date": "2026-08-30", "time": "12:00", "league": "LCK",
            "competition": "LCK", "blue": "T1", "red": "BNK FearX",
            "blueCode": "T1", "redCode": "BFX", "blueScore": 2, "redScore": 1,
            "status": "completed",
        }
        history = {**base, "competition": "T1", "league": "T1"}
        self.assertEqual(len(server.merge_match_records([base, history])), 1)

    def test_completed_duplicate_wins_over_upcoming_record(self):
        completed = {
            "date": "2026-08-30", "time": "12:00", "blue": "T1", "red": "HLE",
            "blueCode": "T1", "redCode": "HLE", "blueScore": 2, "redScore": 3,
            "status": "completed",
        }
        upcoming = {**completed, "blueScore": None, "redScore": None, "status": "upcoming"}
        merged = server.merge_match_records([upcoming, completed])
        self.assertEqual(len(merged), 1)
        self.assertEqual(
            (merged[0]["blueScore"], merged[0]["redScore"], merged[0]["status"]),
            (2, 3, "completed"),
        )

    def test_merge_rejects_invalid_completed_placeholder_result(self):
        stale = {
            "date": "2026-09-06", "time": "12:00", "blue": "T1", "red": "TBD",
            "blueCode": "T1", "redCode": "TBD", "blueScore": 8, "redScore": 0,
            "series": "BO3", "status": "completed",
        }
        merged = server.merge_match_records([stale])
        self.assertEqual(
            (merged[0]["blueScore"], merged[0]["redScore"], merged[0]["status"]),
            (None, None, "upcoming"),
        )

    def test_fast_schedule_cache_loads_only_t1_history(self):
        server.SCHEDULE_CACHE["expires"] = server.datetime.min.replace(
            tzinfo=server.timezone.utc
        )
        with patch.object(server, "load_official_index", return_value={"by_date": {}}), \
             patch.object(
                 server, "fetch_page",
                 return_value=leaguepedia_row("T1", "Gen.G", 2, 1),
             ) as fetch_page:
            first = server.load_schedule_matches()
            second = server.load_schedule_matches()
        self.assertEqual(len(first), 1)
        self.assertEqual(second, first)
        fetch_page.assert_called_once_with("T1/Match_History")

    def test_fast_schedule_failure_returns_stale_cache(self):
        stale_match = {
            "id": "cached",
            "date": "2026-08-30",
            "time": "12:00",
            "blue": "T1",
            "red": "Gen.G",
            "blueCode": "T1",
            "redCode": "GEN",
            "status": "upcoming",
        }
        server.SCHEDULE_CACHE["matches"] = [stale_match]
        with patch.object(server, "load_official_index", return_value={"by_date": {}}), \
             patch.object(server, "fetch_page", side_effect=OSError("offline")):
            self.assertEqual(server.load_schedule_matches(), [stale_match])
        self.assertTrue(server.SCHEDULE_CACHE["stale"])
        self.assertIn("offline", server.SCHEDULE_CACHE["error"])

    def test_fast_schedule_failure_without_stale_returns_empty_list(self):
        with patch.object(server, "load_official_index", return_value={"by_date": {}}), \
             patch.object(server, "fetch_page", side_effect=OSError("offline")):
            self.assertEqual(server.load_schedule_matches(), [])
        self.assertFalse(server.SCHEDULE_CACHE["stale"])
        self.assertIn("offline", server.SCHEDULE_CACHE["error"])

    def test_load_standings_prefers_cached_matches_even_if_expired(self):
        server.CACHE["matches"] = [{
            "date": "2026-08-30",
            "time": "12:00",
            "league": "LCK",
            "competition": "LCK",
            "stage": "Rounds 3-4",
            "blue": "T1",
            "red": "Gen.G",
            "blueCode": "T1",
            "redCode": "GEN",
            "blueScore": 2,
            "redScore": 1,
            "status": "completed",
        }]
        server.CACHE["stage"]["label"] = "Rounds 3-4"
        with patch.object(server, "load_matches", side_effect=AssertionError(
            "load_matches should not run when CACHE['matches'] is populated"
        )):
            rows = server.load_standings()
        self.assertEqual([row["code"] for row in rows], ["T1", "GEN"])
        self.assertFalse(server.STANDINGS_CACHE["stale"])

    def test_load_standings_returns_stale_rows_after_refresh_failure(self):
        server.STANDINGS_CACHE["rows"] = [{"team": "T1", "code": "T1", "rank": 1}]
        with patch.object(server, "load_matches", side_effect=OSError("offline")):
            rows = server.load_standings()
        self.assertEqual(rows, [{"team": "T1", "code": "T1", "rank": 1}])
        self.assertTrue(server.STANDINGS_CACHE["stale"])
        self.assertIn("offline", server.STANDINGS_CACHE["error"])

    def test_game_details_return_unavailable_games_for_bad_feeds(self):
        official = {
            "by_id": {
                "42": {
                    "startTime": "2026-08-30T04:00:00+00:00",
                    "teamIds": {"team:1": "T1", "team:2": "GEN"},
                    "gameIds": [{"id": "1001", "number": 1, "state": "COMPLETED"}],
                }
            }
        }
        with patch.object(server, "load_official_index", return_value=official), \
             patch.object(server, "fetch_feed", return_value={"frames": []}):
            self.assertEqual(
                server.load_game_details("42"),
                {
                    "matchId": "42",
                    "games": [{
                        "number": 1, "state": "COMPLETED", "available": False,
                        "reason": "feed_unavailable",
                        "message": "The official live-stats feed is unavailable.",
                    }],
                },
            )

    def test_game_details_try_the_recorded_game_start_before_match_offsets(self):
        official = {
            "by_id": {
                "42": {
                    "startTime": "2026-08-30T04:00:00+00:00",
                    "teamIds": {"team:1": "T1", "team:2": "GEN"},
                    "gameIds": [{
                        "id": "1001", "number": 2, "state": "completed",
                        "startTime": "2026-08-30T05:15:00Z",
                    }],
                }
            }
        }
        window = {
            "gameMetadata": {
                "blueTeamMetadata": {"esportsTeamId": "team:1", "participantMetadata": []},
                "redTeamMetadata": {"esportsTeamId": "team:2", "participantMetadata": []},
            },
            "frames": [{"blueTeam": {}, "redTeam": {}}],
        }
        details = {"frames": [{"participants": [{}]}]}
        requested_timestamps = []

        def feed(path, starting_time=None):
            requested_timestamps.append(starting_time)
            return details if path.startswith("details/") else window

        with patch.object(server, "load_official_index", return_value=official), \
             patch.object(server, "fetch_feed", side_effect=feed):
            result = server.load_game_details("42")
        self.assertEqual(
            requested_timestamps,
            ["2026-08-30T05:15:00Z", "2026-08-30T05:15:00Z"],
        )
        self.assertEqual(result["games"][0]["number"], 2)

    def test_game_details_use_validated_schedule_fallback(self):
        fallback = server.detail_fallback_match("42", {
            "gameIds": [json.dumps([{"id": "1001", "number": 1, "state": "completed"}])],
            "startTime": ["2026-08-30T04:00:00Z"],
            "teamIds": [json.dumps({"team:1": "T1", "team:2": "GEN"})],
        })
        self.assertEqual(fallback["gameIds"][0]["id"], "1001")
        self.assertEqual(fallback["teamIds"]["team:2"], "GEN")
        with patch.object(server, "load_official_index", return_value={"by_id": {}}), \
             patch.object(server, "fetch_feed", return_value={"frames": []}):
            result = server.load_game_details("42", fallback)
        self.assertEqual(result["games"][0]["reason"], "feed_unavailable")

    def test_game_details_use_fallback_games_missing_from_current_index(self):
        fallback = {
            "matchId": "42",
            "startTime": "2026-08-30T04:00:00+00:00",
            "teamIds": {"team:1": "T1", "team:2": "GEN"},
            "gameIds": [{"id": "1001", "number": 1, "state": "completed"}],
        }
        current_index = {
            "by_id": {
                "42": {
                    "matchId": "42",
                    "startTime": "2026-08-30T04:00:00+00:00",
                    "teamIds": {"team:1": "T1", "team:2": "GEN"},
                    "gameIds": [],
                }
            }
        }
        requested_paths = []

        def feed(path, starting_time=None):
            requested_paths.append(path)
            return {"frames": []}

        with patch.object(server, "load_official_index", return_value=current_index), \
             patch.object(server, "fetch_feed", side_effect=feed):
            result = server.load_game_details("42", fallback)
        self.assertIn("details/1001", requested_paths)
        self.assertEqual(result["games"][0]["number"], 1)

    def test_game_details_normalize_mocked_feeds(self):
        official = {
            "by_id": {
                "42": {
                    "startTime": "2026-08-30T04:00:00+00:00",
                    "teamIds": {"team:1": "T1", "team:2": "GEN"},
                    "gameIds": [{"id": "1001", "number": 1, "state": "completed"}],
                }
            }
        }
        window = {
            "gameMetadata": {
                "patchVersion": "16.15",
                "blueTeamMetadata": {
                    "esportsTeamId": "team:1",
                    "participantMetadata": [{
                        "participantId": 1, "summonerName": "Player1",
                        "championId": "Ahri", "role": "mid",
                    }],
                },
                "redTeamMetadata": {
                    "esportsTeamId": "team:2",
                    "participantMetadata": [{
                        "participantId": 2, "summonerName": "Player2",
                        "championId": "Azir", "role": "mid",
                    }],
                },
            },
            "frames": [{
                "blueTeam": {
                    "participants": [{"participantId": 1, "level": 10}],
                    "totalGold": 1000, "totalKills": 3,
                    "towers": 2, "inhibitors": 1, "barons": 1,
                },
                "redTeam": {
                    "participants": [{"participantId": 2, "level": 9}],
                    "totalGold": 800, "totalKills": 1,
                    "towers": 0, "inhibitors": 0, "barons": 0,
                },
            }],
        }
        details = {
            "frames": [{
                "rfc460Timestamp": "2026-08-30T05:00:00Z",
                "participants": [
                    {"participantId": 1, "kills": 3, "deaths": 0, "assists": 2,
                     "totalGoldEarned": 1000, "creepScore": 100, "items": ["1"]},
                    {"participantId": 2, "kills": 1, "deaths": 3, "assists": 0,
                     "totalGoldEarned": 800, "creepScore": 80, "items": []},
                ],
            }],
        }

        def feed(path, starting_time=None):
            return details if path.startswith("details/") else window

        with patch.object(server, "load_official_index", return_value=official), \
             patch.object(server, "fetch_feed", side_effect=feed):
            result = server.load_game_details("42")
        self.assertEqual(result["games"][0]["teams"]["blue"]["code"], "T1")
        self.assertEqual(result["games"][0]["teams"]["red"]["code"], "GEN")
        self.assertEqual(result["games"][0]["teams"]["blue"]["participants"][0]["kills"], 3)

    def test_game_details_keep_unavailable_games_when_another_game_loads(self):
        official = {
            "by_id": {
                "42": {
                    "startTime": "2026-08-30T04:00:00+00:00",
                    "teamIds": {"team:1": "T1", "team:2": "GEN"},
                    "gameIds": [
                        {"id": "1001", "number": 1, "state": "completed"},
                        {"id": "1002", "number": 2, "state": "completed"},
                    ],
                }
            }
        }
        window = {
            "gameMetadata": {
                "patchVersion": "16.15",
                "blueTeamMetadata": {
                    "esportsTeamId": "team:1",
                    "participantMetadata": [{
                        "participantId": 1, "summonerName": "Player1",
                        "championId": "Ahri", "role": "mid",
                    }],
                },
                "redTeamMetadata": {
                    "esportsTeamId": "team:2",
                    "participantMetadata": [{
                        "participantId": 2, "summonerName": "Player2",
                        "championId": "Azir", "role": "mid",
                    }],
                },
            },
            "frames": [{
                "blueTeam": {"participants": [{"participantId": 1, "level": 10}],
                             "totalGold": 1000, "totalKills": 3},
                "redTeam": {"participants": [{"participantId": 2, "level": 9}],
                            "totalGold": 800, "totalKills": 1},
            }],
        }
        details = {
            "frames": [{
                "rfc460Timestamp": "2026-08-30T05:00:00Z",
                "participants": [
                    {"participantId": 1, "kills": 3, "deaths": 0, "assists": 2},
                    {"participantId": 2, "kills": 1, "deaths": 3, "assists": 0},
                ],
            }],
        }

        def feed(path, starting_time=None):
            if "1001" in path:
                raise OSError("first game's feed is unavailable")
            return details if path.startswith("details/") else window

        with patch.object(server, "load_official_index", return_value=official), \
             patch.object(server, "fetch_feed", side_effect=feed):
            result = server.load_game_details("42")
        self.assertEqual(len(result["games"]), 2)
        self.assertEqual(result["games"][0]["available"], False)
        self.assertEqual(result["games"][1]["teams"]["blue"]["code"], "T1")


if __name__ == "__main__":
    unittest.main()
