import json
import html as html_module
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, urlopen

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8080"))
FANDOM_API = "https://lol.fandom.com/api.php"
OFFICIAL_SCHEDULE_URL = "https://lolesports.com/en-US/schedule"
FEED_API = "https://feed.lolesports.com/livestats/v1"
TEAM_MATCH_HISTORY_PAGES = {"T1": "T1/Match_History"}
CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "matches": [],
    "stage": {"page": "", "label": "", "year": "", "key": "", "refreshedAt": ""},
}
OFFICIAL_CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "by_key": {},
    "by_id": {},
    "diagnostics": {},
}
DETAIL_CACHE = {}
LOGO_CACHE = {}
STANDINGS_CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "rows": [],
    "competition": {"league": "LCK", "label": "LCK", "stage": ""},
}
DEFAULT_STAGE_SUFFIX = "Rounds_3-4"  # Only used when Leaguepedia is unavailable.
HONG_KONG = timezone(timedelta(hours=8))
TEAM_CODE_ALIASES = {
    "BILIBILI": "BLG",
    "BILIBILIGAMING": "BLG",
    "BNKFEARX": "BFX",
    "BNKFEAR": "BFX",
    "FEARX": "BFX",
    "DPLUSKIA": "DK",
    "DPLUS": "DK",
    "DNFREECS": "DNS",
    "DNF": "DNS",
    "DRX": "DRX",
    "GENG": "GEN",
    "GENGESPORTS": "GEN",
    "GEN.G": "GEN",
    "GEN.GESPORTS": "GEN",
    "HANWHALIFEESPORTS": "HLE",
    "HANWHA": "HLE",
    "HANWHALIFE": "HLE",
    "KTROLSTER": "KT",
    "NONGSHIMREDFORCE": "NS",
    "NONGSHIM": "NS",
    "OKSAVINGSBANKBRION": "BRO",
    "BRION": "BRO",
    "T1ESPORTS": "T1",
}
STAGE_CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "pages": [],
    "season_year": None,
}


def team_code(name):
    normalized = re.sub(r"[^A-Z0-9]", "", str(name or "").upper())
    return TEAM_CODE_ALIASES.get(normalized, normalized[:3])


def parse_score(value):
    if value is None:
        return None
    match = re.search(r"\b([0-9]+)\b", html_module.unescape(str(value)))
    return int(match.group(1)) if match else None


UNRESOLVED_OPPONENT_RE = re.compile(
    r"^(?:TBD|TBC|TBA|UNKNOWN|N/?A|"
    r"TO\s+BE\s+DETERMINED|TO\s+BE\s+CONFIRMED|"
    r"(?:WINNER|LOSER)\s+OF\b.*|"
    r"(?:TEAM|SEED|SLOT|BRACKET)\s*[\w#-]*)$",
    re.IGNORECASE,
)


def is_unresolved_opponent(value):
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    return not normalized or bool(UNRESOLVED_OPPONENT_RE.fullmatch(normalized))


def valid_series_score(scores, teams=None, series=None):
    if not isinstance(scores, (list, tuple)) or len(scores) != 2:
        return False
    if teams and any(is_unresolved_opponent(team) for team in teams):
        return False
    if any(not isinstance(score, int) or score < 0 or score > 3 for score in scores):
        return False
    target = 3 if str(series or "").upper() == "BO5" else 2
    return max(scores) == target and min(scores) < target


def sanitize_series_score(scores, teams, series):
    return list(scores) if valid_series_score(scores, teams, series) else [None, None]


def extract_scores(row, team_cells):
    score_pattern = r'class="[^"]*\b(?:matchlist-)?score\b[^"]*"[^>]*>(.*?)</'
    scores = [
        parse_score(re.search(
            score_pattern,
            cell,
            re.DOTALL,
        ).group(1))
        if re.search(score_pattern, cell, re.DOTALL)
        else None
        for cell in team_cells
    ]
    if all(score is not None for score in scores):
        return scores

    score_values = [
        parse_score(match.group(1))
        for match in re.finditer(
            score_pattern,
            row,
            re.DOTALL,
        )
    ]
    if len(score_values) >= 2:
        return score_values[:2]

    attribute_result = re.search(
        r'\bdata-(?:score|result)\s*=\s*["\']\s*([0-9]+)\s*[-–—:]\s*([0-9]+)',
        row,
        re.IGNORECASE,
    )
    if attribute_result:
        return [int(attribute_result.group(1)), int(attribute_result.group(2))]

    text = html_module.unescape(re.sub(r"<[^>]+>", " ", row))
    result = re.search(r"\b([0-9]+)\s*[-–—:]\s*([0-9]+)\b", text)
    if result:
        return [int(result.group(1)), int(result.group(2))]
    return scores


def has_completed_result(scores, row, teams=None, series=None):
    return valid_series_score(scores, teams, series) and bool(
        all(score is not None for score in scores)
        or re.search(r"\b(completed|finished|final|result)\b", row, re.IGNORECASE)
    )


def extract_series(row, scores):
    series_match = re.search(r"\b(?:BO|BEST\s+OF\s*)([357])\b", row, re.IGNORECASE)
    if series_match:
        return f"BO{series_match.group(1)}"
    return "BO5" if any(score is not None and score >= 3 for score in scores) else "BO3"


def fetch_page(page):
    params = f"action=parse&page={quote(page)}&prop=text&format=json&origin=*"
    request = Request(
        f"{FANDOM_API}?{params}",
        headers={"User-Agent": "NexusWatch/0.1 local schedule proxy"},
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["parse"]["text"]["*"]


def fetch_official_schedule():
    request = Request(
        OFFICIAL_SCHEDULE_URL,
        headers={"User-Agent": "NexusWatch/0.1 local schedule index"},
    )
    with urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8")


def season_page(year):
    return f"LCK/{year}_Season"


def default_stage_page(year):
    return f"{season_page(year)}/{DEFAULT_STAGE_SUFFIX}"


def discover_stage_pages():
    now = datetime.now(timezone.utc)
    if STAGE_CACHE["expires"] > now:
        return STAGE_CACHE["pages"]
    pages = []
    current_year = datetime.now(timezone(timedelta(hours=8))).year
    selected_year = None
    for candidate_year in (current_year, current_year - 1):
        try:
            html = fetch_page(season_page(candidate_year))
            pages = list(dict.fromkeys(re.findall(
                rf'href="/wiki/(LCK/{candidate_year}_Season/[^"#?]+)"',
                html,
            )))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"[Leaguepedia] {season_page(candidate_year)} unavailable: {error}")
            continue
        if pages:
            selected_year = candidate_year
            break
    if selected_year is None:
        selected_year = current_year - 1
        pages = [default_stage_page(selected_year)]
    STAGE_CACHE.update({
        "expires": now + timedelta(minutes=10),
        "pages": pages,
        "season_year": selected_year,
    })
    return pages


def stage_label(page):
    return page.rsplit("/", 1)[-1].replace("_", " ")


def match_start(match):
    if match.get("time") == "—":
        return None
    try:
        return datetime.fromisoformat(
            f"{match['date']}T{match['time']}:00+08:00"
        )
    except (KeyError, ValueError):
        return None


def match_fallback_identity(match):
    codes = tuple(sorted({
        team_code(match.get("blueCode") or match.get("blue", "")),
        team_code(match.get("redCode") or match.get("red", "")),
    }))
    return (
        "fallback",
        match.get("date", ""),
        codes,
        match.get("time") or "—",
    )


def match_identity_keys(match):
    keys = []
    if match.get("matchId"):
        keys.append(("official", str(match["matchId"])))
    keys.append(match_fallback_identity(match))
    return keys


def match_identity(match):
    return match_identity_keys(match)[0]


def record_quality(match):
    return (
        bool(match.get("matchId")),
        match.get("blueScore") is not None and match.get("redScore") is not None,
        match.get("status") == "completed",
        match.get("time") not in (None, "", "—"),
        len(match.get("gameIds") or []),
        match.get("league") == "LCK",
    )


def merge_match_record(existing, incoming):
    preferred, fallback = (
        (incoming, existing)
        if record_quality(incoming) > record_quality(existing)
        else (existing, incoming)
    )
    merged = dict(preferred)
    for key, value in fallback.items():
        if value in (None, "", "—", []):
            continue
        if merged.get(key) in (None, "", "—", []):
            merged[key] = value
    if (
        merged.get("blueScore") is not None
        and merged.get("redScore") is not None
    ):
        merged["status"] = "completed"
    if preferred.get("matchId") and not merged.get("matchId"):
        merged["matchId"] = preferred["matchId"]
    return merged


def merge_match_records(records):
    merged = []
    indexes = {}
    for record in records:
        if not record:
            continue
        existing_index = next(
            (indexes[key] for key in match_identity_keys(record) if key in indexes),
            None,
        )
        if existing_index is None:
            existing_index = len(merged)
            merged.append(record)
        else:
            merged[existing_index] = merge_match_record(
                merged[existing_index], record
            )
        for key in match_identity_keys(merged[existing_index]):
            indexes[key] = existing_index
    return sorted(merged, key=lambda match: (
        match.get("date", "9999-12-31"),
        match.get("time") if match.get("time") not in (None, "—") else "99:99",
    ))


def select_active_stage(stage_matches):
    now_hong_kong = datetime.now(HONG_KONG)
    upcoming = []
    live = []
    latest_past = None
    for page, matches in stage_matches.items():
        starts = [(match, match_start(match)) for match in matches]
        starts = [(match, start) for match, start in starts if start]
        if not starts:
            continue
        live_starts = [start for match, start in starts if match["status"] == "live"]
        if live_starts:
            live.append((min(live_starts), page))
        future_starts = [start for match, start in starts if start >= now_hong_kong and match["status"] != "completed"]
        if future_starts:
            upcoming.append((min(future_starts), page))
        stage_latest = max(start for _, start in starts)
        if latest_past is None or stage_latest > latest_past[0]:
            latest_past = (stage_latest, page)
    if live:
        return min(live)[1]
    if upcoming:
        return min(upcoming)[1]
    return latest_past[1] if latest_past else default_stage_page(
        STAGE_CACHE["season_year"] or datetime.now(timezone.utc).year
    )


def parse_official_events(payload):
    normalized = normalize_official_payload(payload)
    decoder = json.JSONDecoder()
    marker = re.compile(r'"__typename"\s*:\s*"EventMatch"')
    events = []
    seen_ids = set()
    for match in marker.finditer(normalized):
        starts = [item.start() for item in re.finditer(r"\{", normalized[:match.start() + 1])]
        for start in reversed(starts):
            try:
                candidate, _ = decoder.raw_decode(normalized[start:])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(candidate, dict) or candidate.get("__typename") != "EventMatch":
                continue
            event_id = candidate.get("id") or candidate.get("matchId")
            if event_id and str(event_id) not in seen_ids:
                seen_ids.add(str(event_id))
                events.append(candidate)
            break
    return events


def normalize_official_event(event):
    event_id = event.get("id") or event.get("matchId")
    start_value = event.get("startTime") or event.get("start_time")
    if not event_id or not start_value:
        return None
    start = parse_start(str(start_value).replace("Z", "+0000"))
    raw_teams = event.get("teams") or event.get("matchTeams") or []
    if not isinstance(raw_teams, list):
        return None
    team_entries = {}
    for team in raw_teams:
        if not isinstance(team, dict):
            continue
        team_id = team.get("id") or team.get("teamId") or team.get("esportsTeamId")
        code = team.get("code") or team.get("shortCode") or team.get("name")
        if team_id and code and not is_unresolved_opponent(code):
            team_entries[str(team_id)] = team_code(code)
    if len(set(team_entries.values())) != 2:
        return None
    team_ids = {}
    for team_id, code in team_entries.items():
        team_ids[team_id] = code
        team_ids[team_id.rsplit(":", 1)[-1]] = code
    raw_games = event.get("games") or event.get("gameIds") or []
    if not isinstance(raw_games, list):
        raw_games = []
    games = []
    game_ids = set()
    for index, game in enumerate(raw_games, 1):
        if not isinstance(game, dict):
            continue
        game_id = game.get("id") or game.get("gameId")
        if not game_id or str(game_id) in game_ids:
            continue
        game_ids.add(str(game_id))
        number = game.get("number") or game.get("gameNumber") or index
        try:
            number = int(number)
        except (TypeError, ValueError):
            number = index
        games.append({
            "id": str(game_id),
            "number": number,
            "state": game.get("state") or game.get("status"),
        })
    entry = {
        "matchId": str(event_id),
        "startTime": start.isoformat(),
        "gameIds": games,
        "teamIds": team_ids,
    }
    competition = (
        event.get("tournamentName")
        or event.get("leagueName")
        or event.get("eventName")
    )
    if competition:
        entry["competition"] = competition
    return entry


def load_official_index():
    now = datetime.now(timezone.utc)
    if OFFICIAL_CACHE["expires"] > now:
        return OFFICIAL_CACHE
    by_key = {}
    by_date = {}
    by_id = {}
    diagnostics = {"events": 0, "normalized": 0, "skipped": 0}
    try:
        events = parse_official_events(fetch_official_schedule())
        diagnostics["events"] = len(events)
        entries_by_id = {}
        for event in events:
            try:
                entry = normalize_official_event(event)
            except (TypeError, ValueError, KeyError, IndexError):
                entry = None
            if not entry:
                diagnostics["skipped"] += 1
                continue
            diagnostics["normalized"] += 1
            existing = entries_by_id.get(entry["matchId"])
            if existing:
                existing["gameIds"] = merge_game_ids(
                    existing["gameIds"], entry["gameIds"]
                )
                existing["teamIds"].update(entry["teamIds"])
                if entry.get("competition") and not existing.get("competition"):
                    existing["competition"] = entry["competition"]
            else:
                entries_by_id[entry["matchId"]] = entry
        for entry in entries_by_id.values():
            local_date = datetime.fromisoformat(entry["startTime"]).astimezone(
                HONG_KONG
            ).strftime("%Y-%m-%d")
            codes = tuple(sorted(set(entry["teamIds"].values())))
            if len(codes) != 2:
                continue
            by_key.setdefault((local_date, codes), []).append(entry)
            by_date.setdefault(local_date, []).append(entry)
            by_id[entry["matchId"]] = entry
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"[LoL Esports] official schedule index unavailable: {error}")
    diagnostics["indexed"] = len(by_id)
    OFFICIAL_CACHE.update({
        "expires": now + timedelta(minutes=10),
        "by_key": by_key,
        "by_date": by_date,
        "by_id": by_id,
        "diagnostics": diagnostics,
    })
    return OFFICIAL_CACHE


def merge_game_ids(existing, incoming):
    merged = {}
    for game in (existing or []) + (incoming or []):
        if not isinstance(game, dict) or not game.get("id"):
            continue
        key = (str(game["id"]), game.get("number"))
        previous = merged.get(key)
        if not previous or game_state_quality(game) > game_state_quality(previous):
            merged[key] = game
    return list(merged.values())


def game_state_quality(game):
    state = str(game.get("state", "")).lower()
    return (
        state in {"completed", "complete", "finished", "ended"},
        state in {"inprogress", "in_progress", "live"},
    )


def normalize_official_payload(payload):
    normalized = html_module.unescape(payload or "")
    for _ in range(2):
        normalized = normalized.replace(r"\"", '"').replace(r"\u0022", '"')
    return normalized


def find_official_match(official_index, date, codes, match_time=None):
    index = official_index or {}
    by_key = index.get("by_key", index) if isinstance(index, dict) else {}
    if any(is_unresolved_opponent(code) for code in codes):
        return None
    normalized_codes = tuple(sorted(team_code(code) for code in codes))
    if any(is_unresolved_opponent(code) for code in normalized_codes):
        return None
    if len(set(normalized_codes)) != 2:
        return None
    keyed = by_key.get((date, normalized_codes), [])
    if isinstance(keyed, dict):
        keyed = [keyed]
    keyed_ids = {entry.get("matchId") for entry in keyed if entry.get("matchId")}
    if match_time:
        timed = [
            entry for entry in keyed
            if official_local_time(entry) == match_time
        ]
        timed_ids = {entry.get("matchId") for entry in timed if entry.get("matchId")}
        if len(timed_ids) == 1:
            return timed[0]
    if len(keyed_ids) == 1:
        return keyed[0]
    if len(keyed_ids) > 1:
        return None
    candidates = index.get("by_date", {}).get(date, []) if isinstance(index, dict) else []
    wanted = set(normalized_codes)
    exact = [
        candidate for candidate in candidates
        if len(set(candidate.get("teamIds", {}).values())) == 2
        and wanted == set(candidate.get("teamIds", {}).values())
    ]
    exact_ids = {entry.get("matchId") for entry in exact if entry.get("matchId")}
    if match_time:
        timed = [
            entry for entry in exact
            if official_local_time(entry) == match_time
        ]
        timed_ids = {entry.get("matchId") for entry in timed if entry.get("matchId")}
        if len(timed_ids) == 1:
            return timed[0]
    if len(exact_ids) == 1:
        return exact[0]
    return None


def official_local_time(entry):
    try:
        return datetime.fromisoformat(entry["startTime"]).astimezone(HONG_KONG).strftime("%H:%M")
    except (KeyError, TypeError, ValueError):
        return None


def parse_matches(league, page, html, official_index=None):
    matches = []
    rows = re.findall(
        r'<tr[^>]*class="[^"]*\bml-row\b[^"]*"[^>]*data-date="([^"]+)"[^>]*>(.*?)</tr>',
        html,
        flags=re.DOTALL,
    )
    for fallback_date, row in rows:
        team_cells = re.findall(
            r'<td[^>]*class="[^"]*\bmatchlist-team[12]\b[^"]*"[^>]*>(.*?)</td>',
            row,
            flags=re.DOTALL,
        )
        if len(team_cells) != 2:
            continue
        team_matches = [
            re.search(r'class="teamname"[^>]*>(.*?)</span>', cell, re.DOTALL)
            for cell in team_cells
        ]
        teams = [
            html_module.unescape(re.sub(r"<[^>]+>", "", team.group(1))).strip()
            if team else ""
            for team in team_matches
        ]
        if len(teams) != 2 or any(not team for team in teams):
            continue
        raw_scores = extract_scores(row, team_cells)
        raw_series = extract_series(row, raw_scores)
        scores = sanitize_series_score(raw_scores, teams, raw_series)
        start_match = re.search(r'class="countdowndate">([^<]+)</span>', row)
        start = parse_start(start_match.group(1)) if start_match else None
        date = start.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d") if start else fallback_date
        time = start.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M") if start else "—"
        status = "completed" if has_completed_result(
            scores, row, teams, raw_series
        ) else (
            "live" if re.search(r"\blive\b|\bin[- ]progress\b", row, re.IGNORECASE) else "upcoming"
        )
        match = {
                "id": "",
                "date": date,
                "time": time,
                "status": status,
                "league": league,
                "stage": page.split("/")[-1].replace("_", " "),
                "blue": teams[0].strip(),
                "red": teams[1].strip(),
                "blueCode": team_code(teams[0].strip()),
                "redCode": team_code(teams[1].strip()),
                "blueLogo": extract_logo(team_cells[0]) if len(team_cells) > 0 else None,
                "redLogo": extract_logo(team_cells[1]) if len(team_cells) > 1 else None,
                "blueScore": scores[0],
                "redScore": scores[1],
                "series": raw_series,
                "source": "Leaguepedia",
                "link": f"https://lol.fandom.com/wiki/{quote(page)}",
                "competition": league,
                "officialLinkStatus": "unmatched",
            }
        source_competition = match["competition"]
        match["id"] = "local:" + "|".join(map(str, match_fallback_identity(match)[1:]))
        official = find_official_match(
            official_index,
            date,
            (match["blueCode"], match["redCode"]),
            time,
        )
        if official:
            match["matchId"] = official["matchId"]
            match["startTime"] = official["startTime"]
            match["gameIds"] = official["gameIds"]
            match["teamIds"] = official["teamIds"]
            if source_competition != "LCK" and official.get("competition"):
                match["competition"] = official["competition"]
            match["id"] = official["matchId"]
            match["officialLinkStatus"] = "linked"
        match["teams"] = {
            "blue": {"name": match["blue"], "code": match["blueCode"], "logo": match["blueLogo"]},
            "red": {"name": match["red"], "code": match["redCode"], "logo": match["redLogo"]},
        }
        matches.append(match)
    return matches


def extract_logo(team_cell):
    match = re.search(r'data-src="([^"]+)"', team_cell)
    return match.group(1) if match else None


def fetch_logo(url):
    cached = LOGO_CACHE.get(url)
    if cached:
        return cached
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "static.wikia.nocookie.net":
        raise ValueError("Unsupported logo host")
    request = Request(url, headers={"User-Agent": "NexusWatch/0.1 local logo proxy"})
    with urlopen(request, timeout=20) as response:
        payload = response.read()
        content_type = response.headers.get_content_type()
    result = (payload, content_type)
    LOGO_CACHE[url] = result
    return result


def fetch_feed(path, starting_time=None):
    url = f"{FEED_API}/{path}"
    if starting_time:
        url = f"{url}?startingTime={quote(starting_time)}"
    request = Request(url, headers={"User-Agent": "NexusWatch/0.1 match details"})
    with urlopen(request, timeout=20) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else None


def useful_details(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("frames"), list) or not payload["frames"]:
        return False
    frame = payload["frames"][-1]
    if not isinstance(frame, dict):
        return False
    participants = frame.get("participants", [])
    if not isinstance(participants, list):
        return False
    return any(isinstance(participant, dict) for participant in participants)


def determine_game_winner(teams, state):
    if str(state or "").lower() not in {"finished", "completed", "complete", "ended"}:
        return None
    ranked = sorted(
        teams.values(),
        key=lambda team: (
            team["inhibitors"],
            team["towers"],
            team["barons"],
            team["totalGold"],
            team["kills"],
        ),
        reverse=True,
    )
    if len(ranked) < 2 or (
        ranked[0]["inhibitors"],
        ranked[0]["towers"],
        ranked[0]["barons"],
        ranked[0]["totalGold"],
        ranked[0]["kills"],
    ) == (
        ranked[1]["inhibitors"],
        ranked[1]["towers"],
        ranked[1]["barons"],
        ranked[1]["totalGold"],
        ranked[1]["kills"],
    ):
        return None
    return ranked[0]["code"]


def normalize_game_details(game, window, details, team_ids):
    metadata = window.get("gameMetadata", {}) if isinstance(window, dict) else {}
    window_frames = window.get("frames", []) if isinstance(window, dict) else []
    detail_frames = details.get("frames", []) if isinstance(details, dict) else []
    if not isinstance(metadata, dict) or not window_frames or not detail_frames:
        raise ValueError("Incomplete match detail snapshot")
    window_frame = window_frames[-1]
    details_frame = detail_frames[-1]
    if not isinstance(window_frame, dict) or not isinstance(details_frame, dict):
        raise ValueError("Incomplete match detail snapshot")
    detail_participants = {
        participant["participantId"]: participant
        for participant in details_frame.get("participants", [])
        if isinstance(participant, dict) and "participantId" in participant
    }
    window_participants = {
        participant["participantId"]: participant
        for side in ("blueTeam", "redTeam")
        for participant in (
            window_frame.get(side, {}).get("participants", [])
            if isinstance(window_frame.get(side, {}), dict)
            else []
        )
        if isinstance(participant, dict) and "participantId" in participant
    }
    teams = {}
    for side in ("blue", "red"):
        metadata_key = f"{side}TeamMetadata"
        window_key = f"{side}Team"
        team_metadata = metadata.get(metadata_key, {})
        if not isinstance(team_metadata, dict):
            team_metadata = {}
        team_identifier = (
            team_metadata.get("esportsTeamId")
            or team_metadata.get("teamId")
            or team_metadata.get("id")
        )
        team_code = team_ids.get(team_identifier) or team_ids.get(
            str(team_identifier), "TBD"
        )
        participants = []
        player_metadata = team_metadata.get("participantMetadata", [])
        if not isinstance(player_metadata, list):
            player_metadata = []
        for player in player_metadata:
            if not isinstance(player, dict) or "participantId" not in player:
                continue
            participant_id = player["participantId"]
            detail = detail_participants.get(participant_id, {})
            live = window_participants.get(participant_id, {})
            if not isinstance(detail, dict):
                detail = {}
            if not isinstance(live, dict):
                live = {}
            items = detail.get("items", [])
            if not isinstance(items, list):
                items = []
            participants.append(
                {
                    "player": player.get("summonerName", "Unknown"),
                    "champion": player.get("championId"),
                    "role": player.get("role"),
                    "level": live.get("level", detail.get("level", 0)),
                    "kills": detail.get("kills", 0),
                    "deaths": detail.get("deaths", 0),
                    "assists": detail.get("assists", 0),
                    "gold": detail.get("totalGoldEarned", live.get("totalGold", 0)),
                    "cs": detail.get("creepScore", live.get("creepScore", 0)),
                    "items": items,
                }
            )
        team_stats = window_frame.get(window_key, {})
        if not isinstance(team_stats, dict):
            team_stats = {}
        teams[side] = {
            "code": team_code,
            "totalGold": team_stats.get("totalGold", 0),
            "kills": team_stats.get("totalKills", 0),
            "towers": team_stats.get("towers", 0),
            "inhibitors": team_stats.get("inhibitors", 0),
            "barons": team_stats.get("barons", 0),
            "participants": participants,
        }
    patch = metadata.get("patchVersion", "")
    if not isinstance(patch, str):
        patch = ""
    patch_parts = patch.split(".")
    patch_version = ".".join(patch_parts[:2] + ["1"]) if len(patch_parts) >= 2 else "16.15.1"
    winner = determine_game_winner(teams, game.get("state") or window_frame.get("gameState"))
    return {
        "number": game.get("number"),
        "state": game.get("state"),
        "timestamp": details_frame.get("rfc460Timestamp"),
        "patch": patch_version,
        "winner": winner,
        "teams": teams,
    }


def load_game_details(match_id):
    index = load_official_index()["by_id"]
    match = index.get(str(match_id)) or index.get(match_id)
    if not match:
        raise ValueError("Match details are not linked for this card")
    games = []
    for game in match.get("gameIds") or []:
        if not isinstance(game, dict):
            continue
        game_state = str(game.get("state", "")).lower()
        if not game.get("id"):
            games.append({"number": game.get("number"), "state": game.get("state"), "available": False})
            continue
        if game_state not in {
            "completed", "complete", "finished", "ended",
            "inprogress", "in_progress", "live",
        }:
            games.append({"number": game.get("number"), "state": game.get("state"), "available": False})
            continue
        detail = None
        window = None
        try:
            start = datetime.fromisoformat(match["startTime"])
        except (KeyError, TypeError, ValueError):
            games.append({"number": game.get("number"), "state": game.get("state"), "available": False})
            continue
        probe_offsets = (
            (360, 300, 240, 180, 120, 60, 30, 15, 0, -15)
            if game_state in {"completed", "complete", "finished", "ended"}
            else (180, 120, 60, 30, 15, 0, -15)
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            timestamps = [
                (start + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z")
                for offset in probe_offsets
            ] + [None]
            for timestamp in timestamps:
                detail_future = executor.submit(fetch_feed, f"details/{game['id']}", timestamp)
                window_future = executor.submit(fetch_feed, f"window/{game['id']}", timestamp)
                try:
                    candidate_detail = detail_future.result()
                    candidate_window = window_future.result()
                except (OSError, ValueError, TypeError, KeyError, IndexError, json.JSONDecodeError):
                    continue
                if useful_details(candidate_detail) and isinstance(candidate_window, dict) and candidate_window.get("frames"):
                    detail = candidate_detail
                    window = candidate_window
                    break
        if detail and window:
            try:
                games.append(normalize_game_details(game, window, detail, match.get("teamIds", {})))
            except (TypeError, KeyError, ValueError, IndexError):
                games.append({"number": game.get("number"), "state": game.get("state"), "available": False})
        else:
            games.append({"number": game.get("number"), "state": game.get("state"), "available": False})
    return {"matchId": match_id, "games": games}


def parse_start(value):
    value = html_module.unescape(str(value or "")).strip()
    for pattern in (
        "%d %B %Y %H:%M:%S %z",
        "%d %b %Y %H:%M:%S %z",
        "%d %B %Y %H:%M %z",
        "%d %b %Y %H:%M %z",
    ):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            pass
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_matches():
    now = datetime.now(timezone.utc)
    if CACHE["expires"] > now:
        return CACHE["matches"]
    matches = []
    official_index = load_official_index()
    stage_matches = {}
    pages = discover_stage_pages()
    source_pages = [(page, "LCK") for page in pages]
    source_pages.extend((page, team) for team, page in TEAM_MATCH_HISTORY_PAGES.items())
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(source_pages)))) as executor:
        futures = {
            (page, league): executor.submit(fetch_page, page)
            for page, league in source_pages
        }
        for (page, league), future in futures.items():
            try:
                parsed = parse_matches(league, page, future.result(), official_index)
                if league == "LCK":
                    stage_matches[page] = parsed
                matches.extend(parsed)
            except Exception as error:
                print(f"[Leaguepedia] {page}: {error}")
    matches = merge_match_records(matches)
    active_page = select_active_stage(stage_matches)
    CACHE["stage"] = {
        "page": active_page,
        "label": stage_label(active_page),
        "key": active_page.rsplit("/", 1)[-1],
        "year": active_page.split("/")[1].split("_")[0],
        "refreshedAt": now.isoformat(),
    }
    CACHE["matches"] = matches
    CACHE["expires"] = now + timedelta(minutes=10)
    return matches


def normalize_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def requested_range(query):
    today = datetime.now(HONG_KONG).date()
    start = normalize_date(query.get("from", [None])[0]) or today - timedelta(days=1)
    end = normalize_date(query.get("to", [None])[0]) or today + timedelta(days=1)
    if end < start:
        start, end = end, start
    if (end - start).days > 31:
        end = start + timedelta(days=31)
    return start, end


def team_matches(match, team):
    if not team:
        return True
    wanted = team.strip().upper()
    return wanted in {match.get("blueCode", "").upper(), match.get("redCode", "").upper(),
                      match.get("blue", "").upper(), match.get("red", "").upper()}


def competition_name(match):
    return match.get("competition") or match.get("league")


def select_competition(matches):
    now = datetime.now(HONG_KONG)
    candidates = [
        match for match in matches
        if team_matches(match, "T1")
        and competition_name(match)
        and competition_name(match) != "T1"
    ]
    live = [
        match for match in candidates
        if match.get("status") == "live"
    ]
    upcoming = [
        match for match in candidates
        if (start := match_start(match)) and start >= now and match.get("status") != "completed"
    ]
    past = [
        match for match in candidates
        if (start := match_start(match)) and start < now
    ]
    selected = (
        min(live, key=lambda match: match_start(match))
        if live else
        min(upcoming, key=lambda match: match_start(match))
        if upcoming else
        max(past, key=lambda match: match_start(match), default=None)
    )
    if not selected:
        return {"league": "LCK", "label": "LCK", "stage": ""}
    return {
        "league": competition_name(selected),
        "label": competition_name(selected),
        "stage": selected.get("stage", ""),
    }


def build_standings(matches):
    teams = {}
    for match in matches:
        if match.get("blueScore") is None or match.get("redScore") is None:
            continue
        for side, opponent in (("blue", "red"), ("red", "blue")):
            code = match[f"{side}Code"]
            team = teams.setdefault(code, {
                "team": match[side], "code": code, "logo": match.get(f"{side}Logo"),
                "wins": 0, "losses": 0, "gameWins": 0, "gameLosses": 0,
            })
            score = match[f"{side}Score"]
            opponent_score = match[f"{opponent}Score"]
            team["gameWins"] += score
            team["gameLosses"] += opponent_score
            if score > opponent_score:
                team["wins"] += 1
            else:
                team["losses"] += 1
    rows = sorted(
        teams.values(),
        key=lambda row: (-row["wins"], row["losses"], -(row["gameWins"] - row["gameLosses"]), row["team"]),
    )
    for rank, row in enumerate(rows, 1):
        row.update({
            "rank": rank,
            "matchRecord": f"{row['wins']}-{row['losses']}",
            "gameRecord": f"{row['gameWins']}-{row['gameLosses']}",
            "points": row["wins"],
            "isFavorite": row["code"] == "T1",
        })
    return rows


def load_standings():
    now = datetime.now(timezone.utc)
    if STANDINGS_CACHE["expires"] > now:
        return STANDINGS_CACHE["rows"]
    matches = load_matches()
    competition = select_competition(matches)
    rows = build_standings(
        match for match in matches if competition_name(match) == competition["league"]
    )
    if len(rows) < 4 and competition["league"] != "LCK":
        competition = {"league": "LCK", "label": "LCK", "stage": CACHE["stage"]["label"]}
        rows = build_standings(
            match for match in matches if competition_name(match) == "LCK"
        )
    STANDINGS_CACHE.update({
        "expires": now + timedelta(minutes=10),
        "rows": rows,
        "competition": competition,
    })
    return rows


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path)
        if path.path == "/api/logo":
            requested_url = parse_qs(path.query).get("url", [None])[0]
            if not requested_url:
                self.send_error(400, "Missing logo URL")
                return
            try:
                body, content_type = fetch_logo(unquote(requested_url))
            except (OSError, ValueError) as error:
                self.send_error(502, f"Logo proxy failed: {error}")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.path == "/api/match-details":
            match_id = parse_qs(path.query).get("matchId", [None])[0]
            if not match_id:
                self.send_error(400, "Missing match ID")
                return
            try:
                if match_id not in DETAIL_CACHE:
                    DETAIL_CACHE[match_id] = load_game_details(match_id)
                body = json.dumps(DETAIL_CACHE[match_id]).encode()
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self.send_error(502, f"Match details unavailable: {error}")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "public, max-age=60")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.path == "/api/standings":
            standings = load_standings()
            body = json.dumps({
                "league": STANDINGS_CACHE["competition"]["league"],
                "season": CACHE["stage"]["year"],
                "competition": STANDINGS_CACHE["competition"],
                "standings": standings,
                "cached": STANDINGS_CACHE["expires"].isoformat(),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "public, max-age=600")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.path != "/api/matches":
            return super().do_GET()
        matches = load_matches()
        query = parse_qs(path.query)
        start, end = requested_range(query)
        team = query.get("team", [None])[0]
        result = [
            match for match in matches
            if start <= (normalize_date(match.get("date")) or start) <= end and team_matches(match, team)
        ]
        body = json.dumps({
            "matches": result,
            "stage": CACHE["stage"],
            "range": {"from": start.isoformat(), "to": end.isoformat()},
            "cached": CACHE["expires"].isoformat(),
            "hasMore": True,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"Nexus Watch running at http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
