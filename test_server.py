import json
import unittest
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
    def test_team_aliases_are_canonical(self):
        self.assertEqual(server.team_code("Gen.G"), "GEN")
        self.assertEqual(server.team_code("FearX"), "BFX")
        self.assertEqual(server.team_code("Hanwha Life Esports"), "HLE")

    def test_parse_matches_uses_the_two_team_cells(self):
        parsed = server.parse_matches("LCK", "LCK/2026_Season/Stage", leaguepedia_row(
            "T1", "Gen.G", 2, 1
        ))
        self.assertEqual(len(parsed), 1)
        self.assertEqual(
            (parsed[0]["blue"], parsed[0]["red"], parsed[0]["blueScore"], parsed[0]["redScore"]),
            ("T1", "Gen.G", 2, 1),
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


if __name__ == "__main__":
    unittest.main()
