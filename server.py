import json
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
OFFICIAL_CACHE = {"expires": datetime.min.replace(tzinfo=timezone.utc), "by_key": {}, "by_id": {}}
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
    "BNKFEARX": "BFX",
    "BNKFEAR": "BFX",
    "DPLUSKIA": "DK",
    "DPLUS": "DK",
    "DNFREECS": "DNS",
    "DNF": "DNS",
    "GENG": "GEN",
    "HANWHALIFEESPORTS": "HLE",
    "HANWHA": "HLE",
    "KTROLSTER": "KT",
    "NONGSHIMREDFORCE": "NS",
    "NONGSHIM": "NS",
    "OKSAVINGSBANKBRION": "BRO",
    "BRION": "BRO",
}
STAGE_CACHE = {
    "expires": datetime.min.replace(tzinfo=timezone.utc),
    "pages": [],
    "season_year": None,
}


def team_code(name):
    normalized = re.sub(r"[^A-Z0-9]", "", name.upper())
    return TEAM_CODE_ALIASES.get(normalized, normalized[:3])


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


def load_official_index():
    now = datetime.now(timezone.utc)
    if OFFICIAL_CACHE["expires"] > now:
        return OFFICIAL_CACHE
    by_key = {}
    by_date = {}
    by_id = {}
    try:
        html = fetch_official_schedule()
        marker = re.compile(
            r'\{\s*"__typename"\s*:\s*"EventMatch"\s*,\s*"id"\s*:\s*"\d+"'
        )
        starts = [match.start() for match in marker.finditer(html)]
        if not starts:
            # Some Next.js responses put the schedule JSON inside an escaped
            # script string instead of returning it as a raw JSON fragment.
            html = html.replace('\\"', '"')
            starts = [match.start() for match in marker.finditer(html)]
        chunks = [
            html[start:end]
            for start, end in zip(starts, starts[1:] + [len(html)])
        ]
        for chunk in chunks:
            event_match = re.search(
                r'\{\s*"__typename"\s*:\s*"EventMatch"\s*,\s*"id"\s*:\s*"(\d+)"',
                chunk,
            )
            start_match = re.search(r'"startTime"\s*:\s*"([^"]+)"', chunk)
            if not event_match or not start_match:
                continue
            team_markers = list(re.finditer(
                r'"__typename"\s*:\s*"MatchTeam"',
                chunk,
            ))
            team_entries = []
            for index, team_marker in enumerate(team_markers):
                end = team_markers[index + 1].start() if index + 1 < len(team_markers) else len(chunk)
                team_fragment = chunk[team_marker.start():end]
                team_id = re.search(r'"id"\s*:\s*"([^"]+)"', team_fragment)
                team_name = re.search(r'"name"\s*:\s*"([^"]+)"', team_fragment)
                team_code_match = re.search(r'"code"\s*:\s*"([^"]+)"', team_fragment)
                code = team_code_match.group(1) if team_code_match else (
                    team_name.group(1) if team_name else ""
                )
                if team_id and code:
                    team_entries.append((team_id.group(1), team_code(code)))
            team_ids = {}
            for team_id, code in team_entries:
                team_ids[team_id] = code
                team_ids[team_id.rsplit(":", 1)[-1]] = code
            game_markers = list(re.finditer(
                r'"__typename"\s*:\s*"Game"',
                chunk,
            ))
            games = []
            for index, game_marker in enumerate(game_markers):
                end = game_markers[index + 1].start() if index + 1 < len(game_markers) else len(chunk)
                game_fragment = chunk[game_marker.start():end]
                game_id = re.search(r'"id"\s*:\s*"(\d+)"', game_fragment)
                state = re.search(r'"state"\s*:\s*"([^"]+)"', game_fragment)
                number = re.search(r'"number"\s*:\s*(\d+)', game_fragment)
                if game_id and state and number:
                    games.append({
                        "id": game_id.group(1),
                        "number": int(number.group(1)),
                        "state": state.group(1),
                    })
            start = parse_start(start_match.group(1).replace("Z", "+0000"))
            local_date = start.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            codes = tuple(sorted(set(team_ids.values())))
            entry = {
                "matchId": event_match.group(1),
                "startTime": start.isoformat(),
                "gameIds": games,
                "teamIds": team_ids,
            }
            competition_match = re.search(
                r'"(?:tournamentName|leagueName|eventName)":"([^"]+)"',
                chunk,
            )
            if competition_match:
                entry["competition"] = competition_match.group(1)
            by_key[(local_date, codes)] = entry
            by_date.setdefault(local_date, []).append(entry)
            by_id[entry["matchId"]] = entry
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"[LoL Esports] official schedule index unavailable: {error}")
    OFFICIAL_CACHE.update({
        "expires": now + timedelta(minutes=10),
        "by_key": by_key,
        "by_date": by_date,
        "by_id": by_id,
    })
    return OFFICIAL_CACHE


def find_official_match(official_index, date, codes):
    index = official_index or {}
    by_key = index.get("by_key", index) if isinstance(index, dict) else {}
    normalized_codes = tuple(sorted(team_code(code) for code in codes))
    official = by_key.get((date, normalized_codes))
    if official:
        return official
    candidates = index.get("by_date", {}).get(date, []) if isinstance(index, dict) else []
    wanted = set(normalized_codes)
    return next(
        (
            candidate for candidate in candidates
            if wanted == set(candidate.get("teamIds", {}).values())
        ),
        None,
    )


def parse_matches(league, page, html, official_index=None):
    matches = []
    rows = re.findall(
        r'<tr[^>]*class="[^"]*\bml-row\b[^"]*"[^>]*data-date="([^"]+)"[^>]*>(.*?)</tr>',
        html,
        flags=re.DOTALL,
    )
    for fallback_date, row in rows:
        teams = re.findall(r'class="teamname">([^<]+)</span>', row)
        scores = re.findall(r'class="[^"]*\bmatchlist-score\b[^"]*"[^>]*>\s*([0-9]+)\s*</td>', row)
        team_cells = re.findall(
            r'<td[^>]*class="[^"]*\bmatchlist-team[12]\b[^"]*"[^>]*>(.*?)</td>',
            row,
            flags=re.DOTALL,
        )
        if len(teams) < 2:
            continue
        start_match = re.search(r'class="countdowndate">([^<]+)</span>', row)
        start = parse_start(start_match.group(1)) if start_match else None
        date = start.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d") if start else fallback_date
        time = start.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M") if start else "—"
        status = "completed" if len(scores) >= 2 else (
            "live" if re.search(r"\blive\b|\bin[- ]progress\b", row, re.IGNORECASE) else "upcoming"
        )
        match = {
                "id": f"{page}:{fallback_date}:{teams[0]}:{teams[1]}",
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
                "blueScore": int(scores[0]) if len(scores) >= 1 else None,
                "redScore": int(scores[1]) if len(scores) >= 2 else None,
                "series": "BO3",
                "source": "Leaguepedia",
                "link": f"https://lol.fandom.com/wiki/{quote(page)}",
                "competition": league,
            }
        source_competition = match["competition"]
        official = find_official_match(
            official_index,
            date,
            (match["blueCode"], match["redCode"]),
        )
        if official:
            match.update(official)
            match["id"] = official["matchId"]
            if source_competition == "LCK":
               match["competition"] = source_competition
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
    if not payload or not payload.get("frames"):
        return False
    return any(
        participant.get("items")
        or participant.get("totalGoldEarned", 0)
        or participant.get("kills", 0)
        or participant.get("deaths", 0)
        for participant in payload["frames"][-1].get("participants", [])
    )


def determine_game_winner(teams, state):
    if state not in {"finished", "completed"}:
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
    metadata = window.get("gameMetadata", {})
    window_frame = window.get("frames", [])[-1]
    details_frame = details.get("frames", [])[-1]
    detail_participants = {
        participant["participantId"]: participant
        for participant in details_frame.get("participants", [])
    }
    window_participants = {
        participant["participantId"]: participant
        for side in ("blueTeam", "redTeam")
        for participant in window_frame.get(side, {}).get("participants", [])
    }
    teams = {}
    for side in ("blue", "red"):
        metadata_key = f"{side}TeamMetadata"
        window_key = f"{side}Team"
        team_metadata = metadata.get(metadata_key, {})
        team_code = team_ids.get(team_metadata.get("esportsTeamId"), "TBD")
        participants = []
        for player in team_metadata.get("participantMetadata", []):
            participant_id = player["participantId"]
            detail = detail_participants.get(participant_id, {})
            live = window_participants.get(participant_id, {})
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
                    "items": detail.get("items", []),
                }
            )
        team_stats = window_frame.get(window_key, {})
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
    patch_parts = patch.split(".")
    patch_version = ".".join(patch_parts[:2] + ["1"]) if len(patch_parts) >= 2 else "16.15.1"
    winner = determine_game_winner(teams, game.get("state") or window_frame.get("gameState"))
    return {
        "number": game["number"],
        "state": game["state"],
        "timestamp": details_frame.get("rfc460Timestamp"),
        "patch": patch_version,
        "winner": winner,
        "teams": teams,
    }


def load_game_details(match_id):
    index = load_official_index()["by_id"]
    match = index.get(match_id)
    if not match:
        raise ValueError("Match details are not linked for this card")
    games = []
    for game in match["gameIds"]:
        if game["state"] not in {"completed", "finished", "inProgress", "in_progress", "live"}:
            continue
        detail = None
        window = None
        start = datetime.fromisoformat(match["startTime"])
        probe_offsets = (
            (360, 300, 240, 180, 120, 60, 30, 15, 0)
            if game["state"] == "completed"
            else (180, 120, 60, 30, 15, 0)
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            for offset in probe_offsets:
                timestamp = (start + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z")
                detail_future = executor.submit(fetch_feed, f"details/{game['id']}", timestamp)
                window_future = executor.submit(fetch_feed, f"window/{game['id']}", timestamp)
                try:
                    candidate_detail = detail_future.result()
                    candidate_window = window_future.result()
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if useful_details(candidate_detail) and candidate_window and candidate_window.get("frames"):
                    detail = candidate_detail
                    window = candidate_window
                    break
        if detail and window:
            games.append(normalize_game_details(game, window, detail, match["teamIds"]))
    return {"matchId": match_id, "games": games}


def parse_start(value):
    for pattern in ("%d %B %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            pass
    return datetime.fromisoformat(value.replace(" ", "T"))


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
    deduped = {}
    for match in matches:
        key = (match["date"], tuple(sorted((match["blueCode"], match["redCode"]))))
        current = deduped.get(key)
        if current is None or (
            current.get("league") == "T1"
            and match.get("league") == "LCK"
        ) or (not current.get("matchId") and match.get("matchId")):
            deduped[key] = match
    matches = list(deduped.values())
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
