"""Transparent, reproducible V1 tip model. See MODEL.md for the rules in Traditional Chinese."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import median
from typing import Any

from . import config
from . import teams as team_map
from . import timeutil

# Markets
MARKET_1X2 = "勝負"
MARKET_OU = "大小球"
PICK_HOME = "主勝"
PICK_DRAW = "和局"
PICK_AWAY = "客勝"
PICK_OVER = "大"
PICK_UNDER = "小"


def _safe_div(n: float | int | None, d: float | int | None) -> float | None:
    if n is None or d is None or d == 0:
        return None
    return float(n) / float(d)


def _poisson(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def _sigmoid_pair_and_draw(strength: float) -> dict[str, float]:
    """Map a signed strength score to 1X2 probabilities. Deterministic, no noise."""
    k = config.PPG_SCALE
    p_home_u = math.exp(k * strength)
    p_away_u = math.exp(k * (-strength))
    p_draw_u = config.DRAW_PEAK * math.exp(-config.DRAW_DECAY * strength * strength)
    total = p_home_u + p_away_u + p_draw_u
    return {
        "home": p_home_u / total,
        "draw": p_draw_u / total,
        "away": p_away_u / total,
    }


def poisson_matrix(lambda_home: float, lambda_away: float, max_goals: int | None = None) -> dict[str, float]:
    cap = max_goals or config.POISSON_MAX_GOALS
    p_home = p_draw = p_away = 0.0
    p_over = 0.0
    line = config.OU_LINE
    for i in range(cap + 1):
        pi = _poisson(i, lambda_home)
        for j in range(cap + 1):
            p = pi * _poisson(j, lambda_away)
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
            if i + j > line:
                p_over += p
    mass = p_home + p_draw + p_away
    if mass <= 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3, "over": 0.5, "under": 0.5}
    return {
        "home": p_home / mass,
        "draw": p_draw / mass,
        "away": p_away / mass,
        "over": p_over,
        "under": 1.0 - p_over,
    }


def _record_rates(record: dict[str, Any] | None) -> dict[str, float | None]:
    if not record:
        return {"played": None, "ppg": None, "gf": None, "ga": None}
    played = record.get("played")
    wins = record.get("win")
    draws = record.get("draw")
    goals = record.get("goals") or {}
    gf = goals.get("for")
    ga = goals.get("against")
    points = None
    if wins is not None and draws is not None:
        points = 3 * int(wins) + int(draws)
    return {
        "played": played,
        "ppg": _safe_div(points, played),
        "gf": _safe_div(gf, played),
        "ga": _safe_div(ga, played),
    }


def parse_standings(standings_payload: dict[str, Any], league_id: int) -> dict[int, dict[str, Any]]:
    """Map team_id -> standing row, plus league-level averages on a synthetic key."""
    out: dict[int, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for block in standings_payload.get("response") or []:
        league = block.get("league") or {}
        if league.get("id") not in (None, league_id) and league_id is not None:
            # still accept if the payload is already league-scoped
            pass
        tables = league.get("standings") or []
        for table in tables:
            for row in table:
                team = (row.get("team") or {})
                tid = team.get("id")
                if tid is None:
                    continue
                parsed = {
                    "team_id": tid,
                    "team_name": team.get("name"),
                    "rank": row.get("rank"),
                    "points": row.get("points"),
                    "goals_diff": row.get("goalsDiff"),
                    "form": row.get("form"),
                    "all": _record_rates(row.get("all")),
                    "home": _record_rates(row.get("home")),
                    "away": _record_rates(row.get("away")),
                }
                out[int(tid)] = parsed
                rows.append(parsed)
    n = len(rows)
    if n:
        def avg(getter) -> float | None:
            vals = [getter(r) for r in rows]
            vals = [v for v in vals if v is not None]
            return sum(vals) / len(vals) if vals else None

        out[-1] = {  # type: ignore[assignment]
            "n_teams": n,
            "avg_gf": avg(lambda r: r["all"]["gf"]),
            "avg_ga": avg(lambda r: r["all"]["ga"]),
            "avg_home_gf": avg(lambda r: r["home"]["gf"]),
            "avg_home_ga": avg(lambda r: r["home"]["ga"]),
            "avg_away_gf": avg(lambda r: r["away"]["gf"]),
            "avg_away_ga": avg(lambda r: r["away"]["ga"]),
        }
    return out


def _ft_goals(fx: dict[str, Any]) -> tuple[int, int] | None:
    status = ((fx.get("fixture") or {}).get("status") or {}).get("short")
    if status not in config.GRADEABLE_STATUS:
        return None
    # Prefer official full-time (90 min) score for league 1X2 and O/U
    score = ((fx.get("score") or {}).get("fulltime") or {})
    if score.get("home") is not None and score.get("away") is not None:
        return int(score["home"]), int(score["away"])
    goals = fx.get("goals") or {}
    if goals.get("home") is not None and goals.get("away") is not None:
        return int(goals["home"]), int(goals["away"])
    return None


def form_from_fixtures(
    fixtures: list[dict[str, Any]],
    team_id: int,
    *,
    before_iso: str | None,
    venue: str | None = None,
    window: int = config.FORM_WINDOW,
) -> dict[str, Any]:
    """Compute recent form from official finished fixtures only, strictly before kickoff."""
    cutoff = timeutil.parse_iso(before_iso) if before_iso else None
    rows: list[dict[str, Any]] = []
    for fx in fixtures:
        fixture = fx.get("fixture") or {}
        kickoff = timeutil.parse_iso(fixture.get("date"))
        if cutoff and kickoff and kickoff >= cutoff:
            continue
        goals = _ft_goals(fx)
        if goals is None:
            continue
        home_id = ((fx.get("teams") or {}).get("home") or {}).get("id")
        away_id = ((fx.get("teams") or {}).get("away") or {}).get("id")
        if team_id not in {home_id, away_id}:
            continue
        is_home = team_id == home_id
        if venue == "home" and not is_home:
            continue
        if venue == "away" and is_home:
            continue
        gf, ga = (goals[0], goals[1]) if is_home else (goals[1], goals[0])
        if gf > ga:
            points = 3
            result = "W"
        elif gf == ga:
            points = 1
            result = "D"
        else:
            points = 0
            result = "L"
        rows.append({
            "date": fixture.get("date"),
            "points": points,
            "gf": gf,
            "ga": ga,
            "result": result,
            "venue": "home" if is_home else "away",
        })
    rows.sort(key=lambda r: r["date"] or "", reverse=True)
    used = rows[:window]
    n = len(used)
    unavailable = n < config.MIN_MATCHES_FORM
    if unavailable:
        return {
            "available": False,
            "played": n,
            "ppg": None,
            "gf": None,
            "ga": None,
            "sequence": "".join(r["result"] for r in reversed(used)),
            "reason": "近況場次不足，已略過此項",
        }
    return {
        "available": True,
        "played": n,
        "ppg": round(sum(r["points"] for r in used) / n, 4),
        "gf": round(sum(r["gf"] for r in used) / n, 4),
        "ga": round(sum(r["ga"] for r in used) / n, 4),
        "sequence": "".join(r["result"] for r in reversed(used)),
        "reason": None,
    }


def _component_strength(home_val: float | None, away_val: float | None, scale: float) -> tuple[float | None, bool]:
    if home_val is None or away_val is None or scale == 0:
        return None, False
    return (home_val - away_val) / scale, True


def ppg_probabilities(snapshot: dict[str, Any]) -> tuple[dict[str, float] | None, list[str], dict[str, Any]]:
    used: list[str] = []
    parts: list[tuple[str, float, float]] = []  # name, value, weight
    home_s = snapshot["home"]
    away_s = snapshot["away"]
    league_s = snapshot.get("league_avgs") or {}

    season, ok = _component_strength(home_s.get("season_ppg"), away_s.get("season_ppg"), 3.0)
    if ok:
        parts.append(("season_ppg", season or 0.0, 0.30))
        used.append("積分榜場均得分")

    venue, ok = _component_strength(home_s.get("home_ppg"), away_s.get("away_ppg"), 3.0)
    if ok:
        parts.append(("venue_ppg", venue or 0.0, 0.25))
        used.append("主客場場均得分")

    form, ok = _component_strength(home_s.get("form_ppg"), away_s.get("form_ppg"), 3.0)
    if ok:
        parts.append(("form_ppg", form or 0.0, 0.25))
        used.append("近況場均得分")

    n_teams = league_s.get("n_teams")
    if home_s.get("rank") and away_s.get("rank") and n_teams and n_teams > 1:
        # Lower rank number is better; positive if home is better placed
        rank_s = (away_s["rank"] - home_s["rank"]) / (n_teams - 1)
        parts.append(("rank", rank_s, 0.20))
        used.append("積分榜排名")

    if not parts:
        return None, used, {"reason": "可用的勝負評分資料不足"}

    weight_sum = sum(p[2] for p in parts)
    strength = sum(p[1] * p[2] for p in parts) / weight_sum
    strength += config.HOME_ADVANTAGE_STRENGTH
    probs = _sigmoid_pair_and_draw(strength)
    meta = {
        "method": "ppg_strength",
        "components": {p[0]: round(p[1], 4) for p in parts},
        "weights_renormalized": {p[0]: round(p[2] / weight_sum, 4) for p in parts},
        "home_advantage": config.HOME_ADVANTAGE_STRENGTH,
        "strength": round(strength, 4),
    }
    return probs, used, meta


def poisson_probabilities(snapshot: dict[str, Any]) -> tuple[dict[str, float] | None, list[str], dict[str, Any]]:
    home_s = snapshot["home"]
    away_s = snapshot["away"]
    avgs = snapshot.get("league_avgs") or {}
    used: list[str] = []

    def blended_rate(season_val, form_val, min_season) -> tuple[float | None, str]:
        if season_val is not None and home_s.get("season_played", 0) >= min_season:
            if form_val is not None:
                return 0.70 * season_val + 0.30 * form_val, "season+form"
            return season_val, "season"
        if form_val is not None:
            return form_val, "form_only"
        return None, "unavailable"

    # Attack / defence rates (goals per game)
    home_att, home_att_src = blended_rate(home_s.get("season_gf"), home_s.get("form_gf"), config.MIN_MATCHES_POISSON)
    away_att, away_att_src = blended_rate(away_s.get("season_gf"), away_s.get("form_gf"), config.MIN_MATCHES_POISSON)
    home_def, home_def_src = blended_rate(home_s.get("season_ga"), home_s.get("form_ga"), config.MIN_MATCHES_POISSON)
    away_def, away_def_src = blended_rate(away_s.get("season_ga"), away_s.get("form_ga"), config.MIN_MATCHES_POISSON)

    # Prefer venue splits when both sides have enough home/away games
    venue_ok = (
        (home_s.get("home_played") or 0) >= config.MIN_MATCHES_VENUE
        and (away_s.get("away_played") or 0) >= config.MIN_MATCHES_VENUE
        and home_s.get("home_gf") is not None
        and away_s.get("away_gf") is not None
        and home_s.get("home_ga") is not None
        and away_s.get("away_ga") is not None
    )

    if venue_ok:
        # λ_home ≈ home team home scoring * away team away conceding / league away GA
        denom_away_ga = avgs.get("avg_away_ga") or avgs.get("avg_gf")
        denom_home_ga = avgs.get("avg_home_ga") or avgs.get("avg_gf")
        if not denom_away_ga or not denom_home_ga:
            venue_ok = False
        else:
            lam_home = (home_s["home_gf"] * away_s["away_ga"]) / denom_away_ga
            lam_away = (away_s["away_gf"] * home_s["home_ga"]) / denom_home_ga
            used.extend(["主場得失球", "客場得失球", "聯賽主客場均數"])
            method = "poisson_venue"
    if not venue_ok:
        if None in (home_att, away_att, home_def, away_def):
            return None, used, {"reason": "得失球資料不足，無法估計入球期望"}
        lam_home = 0.5 * home_att + 0.5 * away_def
        lam_away = 0.5 * away_att + 0.5 * home_def
        lam_home *= config.HOME_ADVANTAGE_LAMBDA
        used.extend(["場均得球", "場均失球"])
        if "form" in (home_att_src + away_att_src + home_def_src + away_def_src):
            used.append("近況得失球")
        method = "poisson_overall"

    lam_home = max(0.05, float(lam_home))
    lam_away = max(0.05, float(lam_away))
    probs = poisson_matrix(lam_home, lam_away)
    meta = {
        "method": method,
        "lambda_home": round(lam_home, 4),
        "lambda_away": round(lam_away, 4),
        "home_att_src": home_att_src,
        "away_att_src": away_att_src,
        "home_advantage_lambda": config.HOME_ADVANTAGE_LAMBDA if method == "poisson_overall" else None,
    }
    return probs, used, meta


def _normalize_implied(odds: dict[str, float]) -> dict[str, float]:
    inv = {k: 1.0 / v for k, v in odds.items() if v and v > 1.0}
    total = sum(inv.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in inv.items()}


def extract_reference_odds(odds_entry: dict[str, Any] | None) -> dict[str, Any]:
    """Pull 1X2 and O/U 2.5 reference odds. Median across books; Bet365 preferred if present.

    Bookmaker names are kept only in the lock snapshot, not as a public recommendation.
    """
    if not odds_entry:
        return {"available": False, "one_x_two": None, "ou_25": None, "source": None}

    books = odds_entry.get("bookmakers") or []
    one_list: list[dict[str, float]] = []
    ou_list: list[dict[str, float]] = []
    bet365_1x2 = None
    bet365_ou = None

    def parse_1x2(values: list[dict[str, Any]]) -> dict[str, float] | None:
        got: dict[str, float] = {}
        for item in values:
            name = str(item.get("value") or "").strip().lower()
            try:
                odd = float(item.get("odd"))
            except (TypeError, ValueError):
                continue
            if name in {"home", "1"}:
                got["home"] = odd
            elif name in {"draw", "x"}:
                got["draw"] = odd
            elif name in {"away", "2"}:
                got["away"] = odd
        return got if len(got) == 3 else None

    def parse_ou(values: list[dict[str, Any]]) -> dict[str, float] | None:
        got: dict[str, float] = {}
        for item in values:
            name = str(item.get("value") or "").strip().lower()
            try:
                odd = float(item.get("odd"))
            except (TypeError, ValueError):
                continue
            if name in {"over 2.5", "over"} or name.startswith("over 2.5"):
                got["over"] = odd
            elif name in {"under 2.5", "under"} or name.startswith("under 2.5"):
                got["under"] = odd
        return got if len(got) == 2 else None

    for book in books:
        book_name = book.get("name") or ""
        is_pref = book_name.replace(" ", "").lower() == "bet365"
        for bet in book.get("bets") or []:
            bname = str(bet.get("name") or "").strip().lower()
            values = bet.get("values") or []
            if bname in {"match winner", "1x2", "home/draw/away", "fulltime result", "match result"}:
                parsed = parse_1x2(values)
                if parsed:
                    one_list.append(parsed)
                    if is_pref:
                        bet365_1x2 = parsed
            elif bname in {"goals over/under", "over/under", "total goals", "goals over/under 2.5"}:
                # Only accept 2.5 — if values already filtered
                parsed = parse_ou(values)
                if parsed:
                    ou_list.append(parsed)
                    if is_pref:
                        bet365_ou = parsed

    def med(rows: list[dict[str, float]], keys: list[str]) -> dict[str, float] | None:
        if not rows:
            return None
        out: dict[str, float] = {}
        for key in keys:
            vals = [r[key] for r in rows if key in r]
            if not vals:
                return None
            out[key] = round(float(median(vals)), 3)
        return out

    source_parts = []
    one = bet365_1x2 or med(one_list, ["home", "draw", "away"])
    ou = bet365_ou or med(ou_list, ["over", "under"])
    if bet365_1x2 or bet365_ou:
        source_parts.append("preferred_if_present")
    if one and not bet365_1x2 and one_list:
        source_parts.append("1x2_median")
    if ou and not bet365_ou and ou_list:
        source_parts.append("ou_median")

    return {
        "available": bool(one or ou),
        "one_x_two": one,
        "ou_25": ou,
        "source": "+".join(source_parts) if source_parts else None,
        "bookmaker_count_1x2": len(one_list),
        "bookmaker_count_ou": len(ou_list),
    }


def build_team_snapshot(
    team_id: int,
    venue: str,
    standing: dict[str, Any] | None,
    fixtures: list[dict[str, Any]],
    kickoff_iso: str,
) -> dict[str, Any]:
    standing = standing or {}
    all_r = standing.get("all") or {}
    venue_r = standing.get(venue) or {}
    form = form_from_fixtures(fixtures, team_id, before_iso=kickoff_iso, venue=None)
    return {
        "team_id": team_id,
        "team_name": standing.get("team_name"),
        "rank": standing.get("rank"),
        "points": standing.get("points"),
        "goals_diff": standing.get("goals_diff"),
        "table_form": standing.get("form"),
        "season_played": all_r.get("played"),
        "season_ppg": all_r.get("ppg"),
        "season_gf": all_r.get("gf"),
        "season_ga": all_r.get("ga"),
        "home_played": (standing.get("home") or {}).get("played") if venue == "home" else None,
        "away_played": (standing.get("away") or {}).get("played") if venue == "away" else None,
        "home_ppg": (standing.get("home") or {}).get("ppg") if venue == "home" else None,
        "away_ppg": (standing.get("away") or {}).get("ppg") if venue == "away" else None,
        "home_gf": (standing.get("home") or {}).get("gf") if venue == "home" else None,
        "home_ga": (standing.get("home") or {}).get("ga") if venue == "home" else None,
        "away_gf": (standing.get("away") or {}).get("gf") if venue == "away" else None,
        "away_ga": (standing.get("away") or {}).get("ga") if venue == "away" else None,
        "form_available": form["available"],
        "form_played": form["played"],
        "form_ppg": form["ppg"],
        "form_gf": form["gf"],
        "form_ga": form["ga"],
        "form_sequence": form["sequence"],
        "form_note": form["reason"],
        # Always keep both venue splits on the raw standing for poisson venue model
        **(
            {}
            if venue != "home"
            else {}
        ),
    }


def enrich_venue_splits(snapshot_team: dict[str, Any], standing: dict[str, Any] | None) -> None:
    if not standing:
        return
    home = standing.get("home") or {}
    away = standing.get("away") or {}
    snapshot_team["home_played"] = home.get("played")
    snapshot_team["home_ppg"] = home.get("ppg")
    snapshot_team["home_gf"] = home.get("gf")
    snapshot_team["home_ga"] = home.get("ga")
    snapshot_team["away_played"] = away.get("played")
    snapshot_team["away_ppg"] = away.get("ppg")
    snapshot_team["away_gf"] = away.get("gf")
    snapshot_team["away_ga"] = away.get("ga")


def build_match_snapshot(
    fixture: dict[str, Any],
    standings_map: dict[int, dict[str, Any]],
    fixtures_for_form: list[dict[str, Any]],
    odds_entry: dict[str, Any] | None,
    league: Any,
) -> dict[str, Any]:
    fx = fixture.get("fixture") or {}
    teams = fixture.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_id = int(home["id"])
    away_id = int(away["id"])
    kickoff = fx.get("date")
    home_st = standings_map.get(home_id)
    away_st = standings_map.get(away_id)
    league_avgs = standings_map.get(-1) or {}  # type: ignore[arg-type]
    home_snap = build_team_snapshot(home_id, "home", home_st, fixtures_for_form, kickoff)
    away_snap = build_team_snapshot(away_id, "away", away_st, fixtures_for_form, kickoff)
    enrich_venue_splits(home_snap, home_st)
    enrich_venue_splits(away_snap, away_st)
    odds = extract_reference_odds(odds_entry)
    home_zh, away_zh, title = team_map.match_title(home.get("name") or "", away.get("name") or "")
    kickoff_dt = timeutil.parse_iso(kickoff)
    return {
        "fixture_id": fx.get("id"),
        "league_id": (fixture.get("league") or {}).get("id") or league.id,
        "league": league.name_zh,
        "home_team_en": home.get("name"),
        "away_team_en": away.get("name"),
        "home_team": home_zh,
        "away_team": away_zh,
        "match": title,
        "kickoff": timeutil.iso_hk(kickoff_dt) if kickoff_dt else kickoff,
        "kickoff_label": timeutil.label_hk(kickoff_dt, with_weekday=True) if kickoff_dt else None,
        "status": (fx.get("status") or {}).get("short"),
        "home": home_snap,
        "away": away_snap,
        "league_avgs": league_avgs,
        "odds": odds,
        "standings_available": bool(home_st and away_st),
        "xg": "不使用。V1 不估計、不填入 xG。",
    }


def _confidence(model_prob: float, edge: float | None) -> float:
    if edge is None:
        return model_prob
    return model_prob + 1.5 * max(edge, 0.0)


def _stake(confidence: float) -> float:
    if confidence >= config.CONF_HIGH_FOR_FULL_STAKE:
        return config.STAKE_STANDARD
    return config.STAKE_SMALL


def evaluate_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Return any market selections that clear the V1 confidence threshold."""
    poisson_probs, poisson_used, poisson_meta = poisson_probabilities(snapshot)
    ppg_probs, ppg_used, ppg_meta = ppg_probabilities(snapshot)

    # Prefer Poisson when available (uses goal data); always keep PPG as fallback for 1X2
    one_probs = poisson_probs if poisson_probs else ppg_probs
    one_method = (poisson_meta if poisson_probs else ppg_meta)
    one_used = poisson_used if poisson_probs else ppg_used
    if one_probs is None:
        one_source = None
    else:
        one_source = "poisson" if poisson_probs else "ppg"

    ou_probs = poisson_probs  # O/U only if we have a goal model
    odds = snapshot.get("odds") or {}
    has_1x2_odds = bool(odds.get("one_x_two"))
    has_ou_odds = bool(odds.get("ou_25"))
    implied_1x2 = _normalize_implied(odds["one_x_two"]) if has_1x2_odds else {}
    implied_ou = _normalize_implied(odds["ou_25"]) if has_ou_odds else {}

    candidates: list[dict[str, Any]] = []

    def maybe_add_1x2(code: str, pick: str) -> None:
        if not one_probs:
            return
        model_p = one_probs[code]
        implied = implied_1x2.get(code)
        edge = (model_p - implied) if implied is not None else None
        conf = _confidence(model_p, edge)
        ok = False
        if has_1x2_odds:
            ok = (
                edge is not None
                and edge >= config.EDGE_MIN
                and model_p >= config.PROB_MIN_1X2_WITH_ODDS
                and conf >= config.CONF_MIN_WITH_ODDS
            )
        else:
            if code == "draw":
                second = max(v for k, v in one_probs.items() if k != "draw")
                ok = model_p >= config.PROB_MIN_DRAW_NO_ODDS and (model_p - second) >= config.DRAW_MARGIN_NO_ODDS
            else:
                ok = model_p >= config.PROB_MIN_1X2_NO_ODDS
        if not ok:
            return
        ref_odds = (odds.get("one_x_two") or {}).get(code) if has_1x2_odds else None
        candidates.append({
            "market": MARKET_1X2,
            "pick": pick,
            "pick_code": code,
            "line": None,
            "model_prob": round(model_p, 4),
            "implied_prob": round(implied, 4) if implied is not None else None,
            "edge": round(edge, 4) if edge is not None else None,
            "confidence": round(conf, 4),
            "reference_odds": ref_odds,
            "stake_units": _stake(conf),
            "inputs_used": one_used,
            "model_meta": one_method,
            "model_source": one_source,
        })

    maybe_add_1x2("home", PICK_HOME)
    maybe_add_1x2("draw", PICK_DRAW)
    maybe_add_1x2("away", PICK_AWAY)

    if ou_probs:
        for code, pick in (("over", PICK_OVER), ("under", PICK_UNDER)):
            model_p = ou_probs[code]
            implied = implied_ou.get(code)
            edge = (model_p - implied) if implied is not None else None
            conf = _confidence(model_p, edge)
            if has_ou_odds:
                ok = (
                    edge is not None
                    and edge >= config.EDGE_MIN_OU
                    and model_p >= config.PROB_MIN_OU_WITH_ODDS
                    and conf >= config.CONF_MIN_WITH_ODDS
                )
            else:
                ok = model_p >= config.PROB_MIN_OU_NO_ODDS
            if not ok:
                continue
            ref_odds = (odds.get("ou_25") or {}).get(code) if has_ou_odds else None
            candidates.append({
                "market": MARKET_OU,
                "pick": f"{pick} {config.OU_LINE:g}",
                "pick_code": code,
                "line": config.OU_LINE,
                "model_prob": round(model_p, 4),
                "implied_prob": round(implied, 4) if implied is not None else None,
                "edge": round(edge, 4) if edge is not None else None,
                "confidence": round(conf, 4),
                "reference_odds": ref_odds,
                "stake_units": _stake(conf),
                "inputs_used": poisson_used,
                "model_meta": poisson_meta,
                "model_source": "poisson",
            })

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates


def rationale_zh(snapshot: dict[str, Any], choice: dict[str, Any]) -> str:
    bits: list[str] = []
    used = choice.get("inputs_used") or []
    if used:
        bits.append("依據" + "、".join(used))
    meta = choice.get("model_meta") or {}
    if meta.get("lambda_home") is not None and meta.get("lambda_away") is not None:
        bits.append(
            f"估計入球期望約主 {meta['lambda_home']:.2f}、客 {meta['lambda_away']:.2f}"
        )
    if choice.get("edge") is not None:
        bits.append("參考賠率相對模型估計有值")
    elif not (snapshot.get("odds") or {}).get("available"):
        bits.append("此場沒有可用參考賠率，門檻較高")
    bits.append("這是模型估計，不保證獲勝")
    # Keep it short
    text = "；".join(bits) + "。"
    return text


def select_official_tips(
    snapshots: list[dict[str, Any]],
    already_locked_fixtures: set[int],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pick at most MAX_TIPS_PER_RUN official proposals, one per match, high confidence only."""
    proposals: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for snap in snapshots:
        fid = snap.get("fixture_id")
        if fid in already_locked_fixtures:
            skipped.append({"fixture_id": fid, "match": snap.get("match"), "reason": "此場已有鎖定提示"})
            continue
        if snap.get("status") not in (None, "NS", "TBD"):
            skipped.append({"fixture_id": fid, "match": snap.get("match"), "reason": f"賽況為 {snap.get('status')}，不提出提示"})
            continue
        cands = evaluate_candidates(snap)
        if not cands:
            skipped.append({"fixture_id": fid, "match": snap.get("match"), "reason": "沒有選項通過高信心門檻"})
            continue
        best = cands[0]
        proposals.append({"snapshot": snap, "choice": best, "alternatives": cands[1:]})

    proposals.sort(key=lambda p: p["choice"]["confidence"], reverse=True)
    kept = proposals[: config.MAX_TIPS_PER_RUN]
    for extra in proposals[config.MAX_TIPS_PER_RUN :]:
        skipped.append({
            "fixture_id": extra["snapshot"].get("fixture_id"),
            "match": extra["snapshot"].get("match"),
            "reason": f"已達本輪上限 {config.MAX_TIPS_PER_RUN} 條，寧缺毋濫",
        })
    summary = {
        "considered": len(snapshots),
        "proposed": len(kept),
        "skipped": skipped,
        "note": "若沒有選項通過門檻，本輪官方提示為零。" if not kept else "已選出通過門檻的少數提示。",
    }
    return kept, summary
