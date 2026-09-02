const TIME_ZONE = "Asia/Hong_Kong";
const REMOTE_API_URL = "https://justin-watch-api.onrender.com";
const API_BASE_URL = window.NEXUS_API_BASE_URL
  || (window.location.hostname.endsWith(".github.io") ? REMOTE_API_URL : "");
const FAVORITE_TEAM = "T1";
const HISTORY_KEY = "nexus-watch-match-history-v2";
const BATCH_KEY = "nexus-watch-batches-v1";
const TEAM_LOGOS = Object.freeze({
  BFX: "assets/team-logos/BFX.png", BRO: "assets/team-logos/BRO.png", DK: "assets/team-logos/DK.png",
  DNS: "assets/team-logos/DNS.png", GEN: "assets/team-logos/GEN.png", HLE: "assets/team-logos/HLE.png",
  KRX: "assets/team-logos/KRX.png", KT: "assets/team-logos/KT.png", NS: "assets/team-logos/NS.png",
  T1: "assets/team-logos/T1.png"
});
const state = {
  dateOffset: 0, matches: [], expandedMatchId: null, details: new Map(),
  detailLoading: new Set(), detailErrors: new Map(), standings: [], loadedDates: new Set(),
  ranges: [], loadingBatch: false, scheduleStale: false
};

const $ = (selector) => document.querySelector(selector);
const dateFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: TIME_ZONE });
const timeFormatter = new Intl.DateTimeFormat("en-HK", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: TIME_ZONE });
const dateKey = (date) => new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: TIME_ZONE }).format(date);
const selectedDate = () => dateKey(new Date(Date.now() + state.dateOffset * 86400000));
const formattedDate = () => dateFormatter.format(new Date(Date.now() + state.dateOffset * 86400000));
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
}[character]));
const dateFromKey = (key) => new Date(`${key}T00:00:00+08:00`);
const shiftDate = (key, days) => dateKey(new Date(dateFromKey(key).getTime() + days * 86400000));

function renderMatches() {
  const visible = state.matches.filter((match) => match.date === selectedDate());
  $("#match-list").innerHTML = visible.map((match) => {
    const live = match.status === "live";
    const completed = match.status === "completed";
    return `<article class="match-card ${state.expandedMatchId === match.id ? "is-expanded" : ""}" data-match-key="${escapeHtml(match.id)}" tabindex="0" aria-expanded="${state.expandedMatchId === match.id}">
      <div class="match-meta"><strong>${escapeHtml(match.time)}</strong><span>${escapeHtml(match.league)}</span><span>${escapeHtml(match.stage)}</span></div>
      <div class="team-column team-one">${teamMarkup(match.blue, match.blueCode, match.blueLogo, match.blueScore, completed && match.blueScore < match.redScore)}</div>
      <div class="match-center"><span class="series">BEST OF ${escapeHtml(match.series.replace("BO", ""))}</span><span class="match-series-score">${escapeHtml(`${match.blueScore ?? "—"} - ${match.redScore ?? "—"}`)}</span><strong class="versus">VS</strong><span class="status ${escapeHtml(match.status)}">${live ? "● Live" : escapeHtml(match.status)}</span>${match.link ? `<a class="source-link" href="${escapeHtml(match.link)}" target="_blank" rel="noreferrer">Leaguepedia ↗</a>` : ""}</div>
      <div class="team-column team-two">${teamMarkup(match.red, match.redCode, match.redLogo, match.redScore, completed && match.redScore < match.blueScore)}</div>
      ${state.expandedMatchId === match.id ? `<section class="match-details">${renderMatchDetails(match)}</section>` : ""}
    </article>`;
  }).join("");
  $("#empty-state").hidden = visible.length !== 0;
  if (!visible.length) {
    const dates = [...state.loadedDates].sort();
    const nextDate = dates.find((date) => date > selectedDate());
    const previousDate = [...dates].reverse().find((date) => date < selectedDate());
    const adjacent = nextDate ? `Next loaded match day: ${dateFormatter.format(dateFromKey(nextDate))}.`
      : previousDate ? `Previous loaded match day: ${dateFormatter.format(dateFromKey(previousDate))}.` : "Load another date range.";
    $("#empty-state").textContent = `No T1 matches scheduled for ${formattedDate()}. ${adjacent}`;
  }
  updateSummary();
  updateBatchStatus();
}

function teamMarkup(name, code, logo, score, dimScore) {
  const logoSource = TEAM_LOGOS[code] || (logo ? `${API_BASE_URL}/api/logo?url=${encodeURIComponent(logo)}` : null);
  const logoMarkup = logoSource
    ? `<img class="team-logo" src="${escapeHtml(logoSource)}" alt="${escapeHtml(name)} logo" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.classList.add('is-visible')" /><span class="team-logo logo-fallback">${escapeHtml(code)}</span>`
    : `<span class="team-logo">${escapeHtml(code)}</span>`;
  return `${logoMarkup}<strong class="team-name">${escapeHtml(name)}</strong><span class="team-score ${dimScore ? "dim" : ""}">${escapeHtml(score ?? "—")}</span>`;
}

function updateSummary() {
  const now = Date.now();
  const upcoming = state.matches.filter((match) => match.status !== "completed" && Date.parse(`${match.date}T${match.time}:00+08:00`) >= now)
    .sort((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`))[0];
  $("#next-opponent").textContent = upcoming ? teamName(upcoming, FAVORITE_TEAM === upcoming.blueCode ? upcoming.redCode : upcoming.blueCode) : "No upcoming match";
  $("#next-match").textContent = upcoming ? `${dateFormatter.format(dateFromKey(upcoming.date))} · ${upcoming.time} HKT` : "Schedule unavailable";
  const wins = state.matches.filter((match) => match.status === "completed" && favoriteScore(match) > opponentScore(match)).length;
  const losses = state.matches.filter((match) => match.status === "completed" && favoriteScore(match) < opponentScore(match)).length;
  $("#team-record").textContent = `${wins}-${losses}`;
}

function favoriteScore(match) { return match.blueCode === FAVORITE_TEAM ? match.blueScore : match.redScore; }
function opponentScore(match) { return match.blueCode === FAVORITE_TEAM ? match.redScore : match.blueScore; }

function updateBatchStatus() {
  const status = state.loadingBatch ? "Loading more schedule days…" : state.scheduleStale ? "Showing cached schedule · refresh when online" : `${state.ranges.length} schedule batch${state.ranges.length === 1 ? "" : "es"} loaded`;
  $("#batch-status").textContent = status;
  $("#updated").textContent = status;
}

function renderStandings() {
  $("#standings-list").innerHTML = state.standings.length
    ? state.standings.map((row) => `<li class="standing-row ${row.isFavorite ? "is-favorite" : ""}" ${row.isFavorite ? 'aria-current="true"' : ""}>
      <span class="standing-rank">${escapeHtml(row.rank)}</span>
      ${teamMarkup(row.team, row.code, row.logo, "", false)}
      <span class="standing-record">${escapeHtml(row.matchRecord)}<small>series</small></span>
      <span class="standing-record">${escapeHtml(row.gameRecord)}<small>games</small></span>
    </li>`).join("")
    : '<li class="detail-message">Standings are currently unavailable.</li>';
}

function renderMatchDetails(match) {
  if (!match.matchId) return `<p class="detail-message">Detailed game data is not linked for this match.</p>`;
  if (state.detailLoading.has(match.id)) return `<p class="detail-message">Loading champion, item, and gold data…</p>`;
  if (state.detailErrors.has(match.id)) return `<p class="detail-message">${escapeHtml(state.detailErrors.get(match.id))}</p>`;
  const details = state.details.get(match.id);
  if (!details) return `<p class="detail-message">Loading champion, item, and gold data…</p>`;
  const availableGames = details.games.filter((game) => game.available !== false);
  return availableGames.length ? availableGames.map((game) => gameDetailMarkup(match, game)).join("") : `<p class="detail-message">No game details are available yet.</p>`;
}

function gameDetailMarkup(match, game) {
  const patch = game.patch || "16.15.1";
  const teams = [game.teams.blue, game.teams.red];
  const winner = game.winner ? teamName(match, game.winner) : null;
  return `<section class="game-detail"><div class="game-detail-heading"><strong>Game ${escapeHtml(game.number)}</strong>${winner ? `<span class="game-winner">${escapeHtml(winner)} won</span>` : ""}<span>${escapeHtml(game.state)}</span><span>Patch ${escapeHtml(patch)}</span></div>
    <div class="game-detail-summary">${teams.map((team) => `<div class="detail-team-summary"><strong>${escapeHtml(teamName(match, team.code))}</strong><span>${escapeHtml(formatGold(team.totalGold))} gold · ${escapeHtml(team.kills)} kills</span></div>`).join('<span class="detail-divider">VS</span>')}</div>
    <div class="player-columns">${teams.map((team, teamIndex) => {
      const opponent = teams[teamIndex === 0 ? 1 : 0];
      return `<div class="player-column"><h4>${escapeHtml(teamName(match, team.code))}</h4>${team.participants.map((player) => participantMarkup(player, patch, opponent.participants.find((candidate) => candidate.role === player.role))).join("")}</div>`;
    }).join("")}</div></section>`;
}

function participantMarkup(player, patch, opponent) {
  const champion = player.champion ? `<img class="champion-icon" src="https://ddragon.leagueoflegends.com/cdn/${encodeURIComponent(patch)}/img/champion/${encodeURIComponent(player.champion)}.png" alt="" loading="lazy" />` : `<span class="champion-icon champion-placeholder">?</span>`;
  const items = player.items.length ? player.items.map((item) => `<img class="item-icon" src="https://ddragon.leagueoflegends.com/cdn/${encodeURIComponent(patch)}/img/item/${encodeURIComponent(item)}.png" alt="" loading="lazy" />`).join("") : '<span class="items-empty">—</span>';
  const goldDifference = player.gold - (opponent?.gold ?? player.gold);
  const csDifference = player.cs - (opponent?.cs ?? player.cs);
  return `<div class="player-row"><div class="player-identity">${champion}<span><strong>${escapeHtml(player.player)}</strong><small>${escapeHtml(player.role || "player")}</small></span></div><span class="player-kda">${escapeHtml(`${player.kills}/${player.deaths}/${player.assists}`)}</span><span class="player-economy"><span class="player-gold">${escapeHtml(formatGold(player.gold))} <span class="stat-difference ${differenceClass(goldDifference)}">(${escapeHtml(formatDifference(goldDifference))})</span></span><small>${escapeHtml(player.cs)} CS <span class="stat-difference ${differenceClass(csDifference)}">(${escapeHtml(formatDifference(csDifference))})</span></small></span><div class="item-list">${items}</div></div>`;
}

function formatGold(value) { return Number(value || 0).toLocaleString(); }
function formatDifference(value) { return `${value > 0 ? "+" : ""}${Number(value || 0).toLocaleString()}`; }
function differenceClass(value) { return value > 0 ? "positive" : value < 0 ? "negative" : "even"; }
function teamName(match, code) { return code === match.blueCode ? match.blue : code === match.redCode ? match.red : code; }

async function loadMatchDetails(match) {
  state.detailLoading.add(match.id); renderMatches();
  try {
    const response = await fetch(`${API_BASE_URL}/api/match-details?matchId=${encodeURIComponent(match.matchId)}`);
    if (!response.ok) throw new Error(`Match details unavailable (${response.status})`);
    state.details.set(match.id, await response.json());
  } catch (error) {
    console.warn(error); state.detailErrors.set(match.id, "Match details are currently unavailable.");
  } finally {
    state.detailLoading.delete(match.id);
    if (state.expandedMatchId === match.id) renderMatches();
  }
}

function updateDate() {
  $("#date-value").textContent = formattedDate();
  $("#date-label").textContent = state.dateOffset === 0 ? "Today" : state.dateOffset < 0 ? "Previous day" : "Next day";
  $("#match-heading").textContent = state.dateOffset === 0 ? "T1 matches today" : `T1 matches · ${formattedDate()}`;
}

function mergeMatches(matches) {
  const deduped = new Map();
  matches.filter(Boolean).forEach((match) => deduped.set(match.id || `${match.date}-${match.blue}-${match.red}`, match));
  return [...deduped.values()].sort((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`));
}

function saveBatches() {
  try {
    localStorage.setItem(BATCH_KEY, JSON.stringify({ ranges: state.ranges, matches: state.matches }));
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.matches.slice(-500)));
  } catch (error) { console.warn("Schedule cache could not be saved.", error); }
}

function loadBatches() {
  try {
    const saved = JSON.parse(localStorage.getItem(BATCH_KEY) || "null");
    if (saved?.matches) {
      state.matches = mergeMatches(saved.matches);
      state.ranges = saved.ranges || [];
      state.matches.forEach((match) => state.loadedDates.add(match.date));
    }
  } catch (error) { console.warn("Stored schedule cache could not be read.", error); }
}

function rangeContains(date) {
  return state.ranges.some((range) => range.from <= date && range.to >= date);
}

async function fetchBatch(from, to) {
  if (state.loadingBatch || rangeContains(from) && rangeContains(to)) return;
  state.loadingBatch = true; updateBatchStatus();
  try {
    const url = `${API_BASE_URL}/api/matches?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&team=${FAVORITE_TEAM}`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Schedule unavailable (${response.status})`);
    const payload = await response.json();
    state.matches = mergeMatches([...state.matches, ...(payload.matches || [])]);
    for (let date = from; date <= to; date = shiftDate(date, 1)) state.loadedDates.add(date);
    state.ranges.push({ from, to }); state.ranges = state.ranges.slice(-12);
    state.scheduleStale = false;
    if (payload.stage?.label) $("#hero-stage").textContent = `${payload.stage.year} ${payload.stage.label.replace("Season ", "")}`;
    saveBatches();
  } catch (error) {
    console.warn(error); state.scheduleStale = true;
  } finally {
    state.loadingBatch = false; updateDate(); renderMatches();
  }
}

async function loadStandings() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/standings?league=LCK`);
    if (!response.ok) throw new Error(`Standings unavailable (${response.status})`);
    state.standings = (await response.json()).standings || [];
  } catch (error) { console.warn(error); }
  renderStandings();
}

async function refreshMatches() {
  state.ranges = []; state.loadedDates.clear(); state.scheduleStale = false;
  const today = selectedDate();
  await fetchBatch(shiftDate(today, -1), shiftDate(today, 1));
  loadStandings();
}

async function moveToMatchDay(direction) {
  const current = selectedDate();
  let target = shiftDate(current, direction);
  state.dateOffset += (Date.parse(`${target}T00:00:00+08:00`) - Date.parse(`${current}T00:00:00+08:00`)) / 86400000;
  updateDate();
  if (!rangeContains(target)) await fetchBatch(shiftDate(target, -1), shiftDate(target, 1));
  renderMatches();
}

$("#previous-day").addEventListener("click", () => moveToMatchDay(-1));
$("#next-day").addEventListener("click", () => moveToMatchDay(1));
$("#refresh-button").addEventListener("click", refreshMatches);
$("#match-list").addEventListener("click", (event) => {
  const card = event.target.closest(".match-card");
  if (!card || event.target.closest("a")) return;
  const match = state.matches.find(({ id }) => id === card.dataset.matchKey);
  if (!match) return;
  if (state.expandedMatchId === match.id) { state.expandedMatchId = null; renderMatches(); return; }
  state.expandedMatchId = match.id; renderMatches();
  if (match.matchId && !state.details.has(match.id) && !state.detailLoading.has(match.id)) loadMatchDetails(match);
});

loadBatches();
updateDate();
renderMatches();
renderStandings();
refreshMatches();
