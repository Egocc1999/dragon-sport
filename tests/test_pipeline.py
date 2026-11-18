"""V1 pipeline unit tests. No live API calls."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import config, fixtures_pub, grade, model, store, teams, timeutil


class TimeTests(unittest.TestCase):
    def test_iso_and_label_are_hong_kong(self):
        dt = datetime.fromisoformat("2026-11-22T15:00:00+00:00")
        iso = timeutil.iso_hk(dt)
        label = timeutil.label_hk(dt)
        self.assertTrue(iso.startswith("2026-11-22T23:00:00"))
        self.assertIn("+08:00", iso)
        self.assertIn("23:00", label)
        self.assertIn("香港時間", label)


class TeamNameTests(unittest.TestCase):
    def test_hk_names(self):
        self.assertEqual(teams.team_name_zh("Arsenal"), "阿仙奴")
        self.assertEqual(teams.team_name_zh("Chelsea"), "車路士")
        self.assertEqual(teams.team_name_zh("Bayern Munich"), "拜仁慕尼黑")
        self.assertEqual(teams.team_name_zh("Paris Saint-Germain"), "巴黎聖日耳門")

    def test_unknown_kept_in_english(self):
        self.assertEqual(teams.team_name_zh("Unmapped FC 99"), "Unmapped FC 99")


class PoissonTests(unittest.TestCase):
    def test_probabilities_sum_to_one(self):
        probs = model.poisson_matrix(1.4, 1.1)
        self.assertAlmostEqual(probs["home"] + probs["draw"] + probs["away"], 1.0, places=5)
        self.assertAlmostEqual(probs["over"] + probs["under"], 1.0, places=5)

    def test_strong_home_favours_home_and_over(self):
        high = model.poisson_matrix(2.4, 0.7)
        low = model.poisson_matrix(0.7, 0.7)
        self.assertGreater(high["home"], low["home"])
        self.assertGreater(high["over"], low["over"])


class FormTests(unittest.TestCase):
    def _fx(self, fid, date, home_id, away_id, hs, aws, status="FT"):
        return {
            "fixture": {"id": fid, "date": date, "status": {"short": status}},
            "teams": {"home": {"id": home_id}, "away": {"id": away_id}},
            "goals": {"home": hs, "away": aws},
            "score": {"fulltime": {"home": hs, "away": aws}},
        }

    def test_form_ignores_future_and_unfinished(self):
        fixtures = [
            self._fx(1, "2026-11-01T15:00:00+00:00", 10, 20, 2, 0),
            self._fx(2, "2026-11-08T15:00:00+00:00", 20, 10, 1, 1),
            self._fx(3, "2026-11-15T15:00:00+00:00", 10, 30, 0, 3),
            self._fx(4, "2026-11-20T15:00:00+00:00", 10, 40, 4, 0, status="NS"),
            self._fx(5, "2026-11-25T15:00:00+00:00", 10, 50, 5, 0, status="FT"),
        ]
        form = model.form_from_fixtures(
            fixtures, 10, before_iso="2026-11-22T00:00:00+08:00"
        )
        self.assertTrue(form["available"])
        self.assertEqual(form["played"], 3)
        self.assertEqual(form["sequence"], "WDL")
        # 2-0 W, 1-1 D, 0-3 L => 4 points / 3
        self.assertAlmostEqual(form["ppg"], 4 / 3, places=3)


class OddsExtractTests(unittest.TestCase):
    def test_median_and_missing(self):
        empty = model.extract_reference_odds(None)
        self.assertFalse(empty["available"])
        entry = {
            "bookmakers": [
                {
                    "name": "A",
                    "bets": [
                        {
                            "name": "Match Winner",
                            "values": [
                                {"value": "Home", "odd": "1.80"},
                                {"value": "Draw", "odd": "3.50"},
                                {"value": "Away", "odd": "4.50"},
                            ],
                        }
                    ],
                },
                {
                    "name": "B",
                    "bets": [
                        {
                            "name": "Match Winner",
                            "values": [
                                {"value": "Home", "odd": "2.00"},
                                {"value": "Draw", "odd": "3.40"},
                                {"value": "Away", "odd": "3.80"},
                            ],
                        }
                    ],
                },
            ]
        }
        got = model.extract_reference_odds(entry)
        self.assertEqual(got["one_x_two"]["home"], 1.9)


class GradeTests(unittest.TestCase):
    def test_1x2_home_win(self):
        tip = {"market": "勝負", "pick_code": "home", "stake_units": 1, "reference_odds": 1.8}
        status, pl, label = grade.grade_pick(tip, 2, 1)
        self.assertEqual(status, "won")
        self.assertAlmostEqual(pl, 0.8)
        self.assertIn("中", label)

    def test_1x2_loss(self):
        tip = {"market": "勝負", "pick_code": "home", "stake_units": 1, "reference_odds": 1.8}
        status, pl, label = grade.grade_pick(tip, 0, 0)
        self.assertEqual(status, "lost")
        self.assertAlmostEqual(pl, -1)

    def test_ou_push(self):
        tip = {"market": "大小球", "pick_code": "over", "line": 2.5, "stake_units": 1, "reference_odds": 1.9}
        status, pl, label = grade.grade_pick(tip, 1, 1)
        # 2 goals is under 2.5, not a push
        self.assertEqual(status, "lost")
        tip2 = {"market": "大小球", "pick_code": "over", "line": 2.0, "stake_units": 1, "reference_odds": 1.9}
        status, pl, label = grade.grade_pick(tip2, 1, 1)
        self.assertEqual(status, "push")
        self.assertEqual(pl, 0)
        self.assertIn("走水", label)

    def test_ou_over_wins(self):
        tip = {"market": "大小球", "pick_code": "over", "line": 2.5, "stake_units": 1, "reference_odds": 1.9}
        status, pl, _ = grade.grade_pick(tip, 2, 1)
        self.assertEqual(status, "won")
        self.assertAlmostEqual(pl, 0.9)

    def test_win_without_odds_has_no_invented_profit(self):
        tip = {"market": "勝負", "pick_code": "home", "stake_units": 1, "reference_odds": None}
        status, pl, _ = grade.grade_pick(tip, 1, 0)
        self.assertEqual(status, "won")
        self.assertIsNone(pl)


class ThresholdTests(unittest.TestCase):
    def test_nothing_clears_when_data_missing(self):
        snap = {
            "home": {"season_ppg": None, "form_ppg": None, "rank": None, "season_played": 0,
                     "season_gf": None, "season_ga": None, "form_gf": None, "form_ga": None,
                     "home_played": 0, "away_played": 0, "home_gf": None, "away_gf": None,
                     "home_ga": None, "away_ga": None, "home_ppg": None, "away_ppg": None},
            "away": {"season_ppg": None, "form_ppg": None, "rank": None, "season_played": 0,
                     "season_gf": None, "season_ga": None, "form_gf": None, "form_ga": None,
                     "home_played": 0, "away_played": 0, "home_gf": None, "away_gf": None,
                     "home_ga": None, "away_ga": None, "home_ppg": None, "away_ppg": None},
            "league_avgs": {},
            "odds": {"available": False},
            "status": "NS",
        }
        self.assertEqual(model.evaluate_candidates(snap), [])

    def test_strong_home_favourite_can_clear_without_odds(self):
        snap = {
            "home": {
                "season_ppg": 2.6, "form_ppg": 2.4, "rank": 1, "season_played": 10,
                "season_gf": 2.3, "season_ga": 0.6, "form_gf": 2.2, "form_ga": 0.5,
                "home_played": 5, "away_played": 5, "home_gf": 2.5, "away_gf": 2.0,
                "home_ga": 0.4, "away_ga": 0.8, "home_ppg": 2.8, "away_ppg": 2.4,
            },
            "away": {
                "season_ppg": 0.7, "form_ppg": 0.6, "rank": 18, "season_played": 10,
                "season_gf": 0.6, "season_ga": 2.0, "form_gf": 0.5, "form_ga": 2.1,
                "home_played": 5, "away_played": 5, "home_gf": 0.8, "away_gf": 0.4,
                "home_ga": 1.6, "away_ga": 2.4, "home_ppg": 0.9, "away_ppg": 0.5,
            },
            "league_avgs": {"n_teams": 20, "avg_gf": 1.3, "avg_ga": 1.3,
                            "avg_home_gf": 1.5, "avg_home_ga": 1.1,
                            "avg_away_gf": 1.1, "avg_away_ga": 1.5},
            "odds": {"available": False},
            "status": "NS",
        }
        cands = model.evaluate_candidates(snap)
        self.assertTrue(cands)
        self.assertEqual(cands[0]["pick_code"] in {"home", "over"}, True)


class StoreStatsTests(unittest.TestCase):
    def test_empty_is_not_a_zero_record(self):
        stats = store.compute_stats([])
        self.assertFalse(stats["has_settled_record"])
        self.assertIsNone(stats["overall"])
        self.assertEqual(stats["by_league"], {})

    def test_aggregates_from_settled_only(self):
        tips = [
            {"status": "locked", "league": "英超", "grade_status": None, "unit_pl": None},
            {"status": "settled", "league": "英超", "grade_status": "won", "unit_pl": 0.8},
            {"status": "settled", "league": "英超", "grade_status": "lost", "unit_pl": -1},
            {"status": "settled", "league": "西甲", "grade_status": "push", "unit_pl": 0},
            {"status": "void", "league": "德甲", "grade_status": "void", "unit_pl": 0},
        ]
        stats = store.compute_stats(tips)
        self.assertTrue(stats["has_settled_record"])
        self.assertEqual(stats["overall"]["wins"], 1)
        self.assertEqual(stats["overall"]["losses"], 1)
        self.assertEqual(stats["overall"]["pushes"], 1)
        self.assertEqual(stats["overall"]["voids"], 1)
        self.assertEqual(stats["overall"]["tips"], 3)
        self.assertAlmostEqual(stats["overall"]["win_rate"], 0.5)
        self.assertAlmostEqual(stats["overall"]["unit_pl"], -0.2)


class LockImmutabilityTests(unittest.TestCase):
    def test_cannot_overwrite_or_edit_core_fields(self):
        import pipeline.paths as paths_mod
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            locked = tmp_path / "locked"
            locked.mkdir()
            with mock.patch.object(paths_mod, "LOCKED_DIR", locked), \
                 mock.patch.object(store.paths, "LOCKED_DIR", locked):
                tip = {
                    "id": "ds-1-home-20261122",
                    "pick": "主勝",
                    "rationale": "原始理據",
                    "status": "locked",
                }
                store.write_new_lock(tip)
                with self.assertRaises(FileExistsError):
                    store.write_new_lock(tip)
                with self.assertRaises(ValueError):
                    store.attach_settlement("ds-1-home-20261122", {"rationale": "被改寫"})
                updated = store.attach_settlement(
                    "ds-1-home-20261122",
                    {
                        "grade_status": "won",
                        "result": "won",
                        "result_label": "中",
                        "score": {"home": 1, "away": 0},
                        "score_label": "1–0",
                        "unit_pl": 0.8,
                        "settled_at": "2026-11-23T08:00:00+08:00",
                        "settled_at_label": "x",
                        "official_status": "FT",
                    },
                )
                self.assertEqual(updated["rationale"], "原始理據")
                self.assertEqual(updated["status"], "settled")
                self.assertEqual(updated["unit_pl"], 0.8)


class FetchGuardTests(unittest.TestCase):
    def test_key_missing_message_is_clear(self):
        from pipeline import api
        msg = api.key_missing_message()
        self.assertIn("找不到 5DollarFootballAPI 金鑰", msg)
        self.assertIn("FIVE_DOLLAR_FOOTBALL_API_KEY", msg)
        self.assertIn("沒有呼叫即時 API", msg)

    def test_remaining_message_is_not_a_daily_cap(self):
        from pipeline import api
        msg = api.remaining_message({"rate_limit_remaining": "59", "rate_limit_limit": "60"})
        self.assertIn("59", msg)
        self.assertIn("60", msg)
        self.assertNotIn("本日剩餘", msg)

    def test_default_leagues_use_verified_provider_ids(self):
        self.assertEqual(
            set(config.DEFAULT_LEAGUES),
            {4160026622, 4212821298, 686337048, 3614399544},
        )
        self.assertEqual(config.API_BASE, "https://api-football.5dollarfootballapi.com")
        self.assertEqual(config.API_KEY_ENV, "FIVE_DOLLAR_FOOTBALL_API_KEY")
        season, leagues = config.load_leagues_config()
        self.assertEqual(season, 2026)
        self.assertEqual([lg.id for lg in leagues], [4160026622, 4212821298, 686337048, 3614399544])

    def test_default_fetch_skips_odds(self):
        from unittest import mock
        from pipeline import fetch

        bundle = {"fixtures": [], "odds_by_fixture": {}, "notes": []}
        with mock.patch.object(fetch, "fetch_all", return_value=bundle) as mocked:
            self.assertEqual(fetch.main([]), 0)
            self.assertFalse(mocked.call_args.kwargs["include_odds"])
            self.assertFalse(mocked.call_args.kwargs["force"])
            self.assertFalse(mocked.call_args.kwargs["verify"])

            mocked.reset_mock()
            self.assertEqual(fetch.main(["--no-odds"]), 0)
            self.assertFalse(mocked.call_args.kwargs["include_odds"])

            mocked.reset_mock()
            self.assertEqual(fetch.main(["--odds"]), 0)
            self.assertTrue(mocked.call_args.kwargs["include_odds"])

            mocked.reset_mock()
            self.assertEqual(fetch.main(["--odds", "--no-odds"]), 0)
            self.assertFalse(mocked.call_args.kwargs["include_odds"])

    def test_read_api_key_prefers_new_env_name(self):
        from unittest import mock
        import os

        with mock.patch.dict(
            os.environ,
            {"FIVE_DOLLAR_FOOTBALL_API_KEY": "new-key", "API_FOOTBALL_KEY": "legacy-key"},
            clear=False,
        ):
            got = config.read_api_key()
            if got != "new-key":
                # Do not print actual key material on failure.
                self.fail(
                    "expected new env name to win; got "
                    + ("None" if got is None else f"a value of length {len(got)}")
                )

    def test_missing_key_does_not_call_live_api(self):
        from unittest import mock
        from pipeline import api, fetch

        with mock.patch.object(config, "read_api_key", return_value=None), \
             mock.patch.object(api, "request") as req:
            with self.assertRaises(api.ApiKeyMissing) as ctx:
                fetch.fetch_all(force=False, include_odds=False, verify=False)
            self.assertIn("5DollarFootballAPI", str(ctx.exception))
            req.assert_not_called()


class StandingsParseTests(unittest.TestCase):
    def test_null_form_and_splits_are_not_invented(self):
        payload = {
            "response": [
                {
                    "league": {
                        "id": 4160026622,
                        "standings": [[
                            {
                                "rank": 1,
                                "points": 10,
                                "goalsDiff": 5,
                                "form": None,
                                "team": {"id": 1, "name": "A"},
                                "all": {
                                    "played": 5,
                                    "win": 3,
                                    "draw": 1,
                                    "lose": 1,
                                    "goals": {"for": 8, "against": 3},
                                },
                                "home": None,
                                "away": None,
                            }
                        ]],
                    }
                }
            ]
        }
        parsed = model.parse_standings(payload, 4160026622)
        self.assertIsNone(parsed[1]["form"])
        self.assertIsNone(parsed[1]["home"]["ppg"])
        self.assertIsNone(parsed[1]["home"]["gf"])
        self.assertAlmostEqual(parsed[1]["all"]["ppg"], 2.0)


if __name__ == "__main__":
    unittest.main()



class PublicFixturesTests(unittest.TestCase):
    def _item(self, fid, date, league_id, home, away, status="NS", hs=None, aws=None):
        return {
            "fixture": {"id": fid, "date": date, "status": {"short": status}},
            "league": {"id": league_id, "name": "England Premier League"},
            "teams": {"home": {"name": home}, "away": {"name": away}},
            "goals": {"home": 0 if hs is None and status == "NS" else hs, "away": 0 if aws is None and status == "NS" else aws},
            "score": {"fulltime": {"home": hs, "away": aws}},
        }

    def test_ns_zero_zero_has_no_score(self):
        leagues = [{"id": 4160026622, "name_zh": "英超"}]
        item = self._item(1, "2026-11-21T20:30:00+08:00", 4160026622, "Man City", "Fulham", "NS", None, None)
        item["goals"] = {"home": 0, "away": 0}
        row = fixtures_pub.public_row(item, leagues)
        self.assertEqual(row["home"], "曼城")
        self.assertEqual(row["away"], "富咸")
        self.assertEqual(row["league"], "英超")
        self.assertEqual(row["status_label"], "未開")
        self.assertIsNone(row["score_label"])
        self.assertIsNone(row["home_goals"])
        self.assertIsNone(row["away_goals"])
        self.assertIn("香港時間", row["kickoff_label"])
        self.assertTrue(row["kickoff"].startswith("2026-11-21T20:30:00"))
        self.assertIn("+08:00", row["kickoff"])

    def test_finished_score_and_kickoff_sort(self):
        leagues = [
            {"id": 4160026622, "name_zh": "英超"},
            {"id": 686337048, "name_zh": "德甲"},
        ]
        early = self._item(10, "2026-11-21T03:30:00+08:00", 686337048, "Bayern Munich", "Cologne", "FT", 3, 0)
        late = self._item(11, "2026-11-21T20:30:00+08:00", 4160026622, "Arsenal", "Leeds", "NS")
        mid = self._item(12, "2026-11-21T04:00:00+08:00", 4160026622, "Liverpool", "Man Utd", "1H", 1, 0)
        doc = fixtures_pub.build_public_doc({"fetched_at": "2026-11-17T18:45:29+08:00", "season": 2026, "leagues": leagues, "fixtures": [late, early, mid]})
        rows = doc["fixtures"]
        self.assertEqual([r["id"] for r in rows], [10, 12, 11])
        self.assertEqual(rows[0]["score_label"], "3-0")
        self.assertEqual(rows[0]["league"], "德甲")
        self.assertEqual(rows[0]["away"], "科隆")
        self.assertEqual(rows[1]["score_label"], "1-0")
        self.assertEqual(rows[1]["status_label"], "上半場")
        self.assertIsNone(rows[2]["score_label"])

    def test_publish_writes_public_file(self):
        raw = {
            "fetched_at": "2026-11-17T18:45:29+08:00",
            "season": 2026,
            "leagues": [{"id": 4160026622, "name_zh": "英超", "name_en": "Premier League"}],
            "fixtures": [self._item(99, "2026-11-23T00:30:00+08:00", 4160026622, "Liverpool", "Man Utd", "NS")],
        }
        import tempfile
        from pipeline import paths
        old = paths.FIXTURES_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                paths.FIXTURES_PATH = Path(tmp) / "fixtures.json"
                doc = fixtures_pub.publish_from_bundle(raw)
                written = json.loads(paths.FIXTURES_PATH.read_text(encoding="utf-8"))
                self.assertEqual(len(written["fixtures"]), 1)
                self.assertEqual(written["fixtures"][0]["match"], "利物浦 對 曼聯")
                self.assertEqual(doc["timezone"], "Asia/Hong_Kong")
        finally:
            paths.FIXTURES_PATH = old
