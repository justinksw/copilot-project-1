const TIME_ZONE = "Asia/Hong_Kong";
const REMOTE_API_URL = "https://justin-watch-api.onrender.com";
const API_BASE_URL = window.NEXUS_API_BASE_URL
  || (window.location.hostname.endsWith(".github.io") ? REMOTE_API_URL : "");
const MATCH_TEAM = "T1";
const HISTORY_KEY = "nexus-watch-match-history-v3";
const BATCH_KEY = "nexus-watch-batches-v3";
const LEGACY_BATCH_KEYS = ["nexus-watch-batches-v1", "nexus-watch-batches-v2"];
const TEAM_CODE_ALIASES = Object.freeze({
  BILIBILI: "BLG", BNKFEAR: "BFX", BNKFEARX: "BFX", FEARX: "BFX",
  DPLUS: "DK", DPLUSKIA: "DK", DNF: "DNS", DNFREECS: "DNS",
  GENG: "GEN", HANWHA: "HLE", HANWHALIFEESPORTS: "HLE",
  KTROLSTER: "KT", NONGSHIM: "NS", NONGSHIMREDFORCE: "NS",
  OKSAVINGSBANKBRION: "BRO", BRION: "BRO", T1ESPORTS: "T1"
});
const TEAM_LOGOS = Object.freeze({
  BFX: "assets/team-logos/BFX.png", BRO: "assets/team-logos/BRO.png", DK: "assets/team-logos/DK.png",
  DNS: "assets/team-logos/DNS.png", GEN: "assets/team-logos/GEN.png", HLE: "assets/team-logos/HLE.png",
  KRX: "assets/team-logos/KRX.png", KT: "assets/team-logos/KT.png", NS: "assets/team-logos/NS.png",
  T1: "assets/team-logos/T1.png"
});
const state = {
  activeTab: "today", matches: [], expandedMatchId: null, details: new Map(),
  detailLoading: new Set(), detailErrors: new Map(), standings: [], standingsMeta: {},
  ranges: [], loadingBatch: false, scheduleStale: false, weekOffset: 0
};

const $ = (selector) => document.querySelector(selector);
const dateFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: TIME_ZONE });
const timeFormatter = new Intl.DateTimeFormat("en-HK", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: TIME_ZONE });
const dateKey = (date) => new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: TIME_ZONE }).format(date);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
}[character]));
const dateFromKey = (key) => new Date(`${key}T00:00:00+08:00`);
const shiftDate = (key, days) => dateKey(new Date(dateFromKey(key).getTime() + days * 86400000));

function mondayOfWeek(key) {
  const midday = new Date(`${key}T12:00:00+08:00`);
  const day = midday.getUTCDay();
  return shiftDate(key, -(day === 0 ? 6 : day - 1));
}

function calculateTabRange(tab, today, weekOffset = 0) {
  const monday = mondayOfWeek(today);
  if (tab === "previous") return { from: shiftDate(monday, (weekOffset - 1) * 7), to: shiftDate(today, weekOffset * 7 - 1) };
  if (tab === "next") return { from: shiftDate(today, weekOffset * 7 + 1), to: shiftDate(monday, (weekOffset + 2) * 7 - 1) };
  return { from: today, to: today };
}

function tabRange(tab, today = dateKey(new Date())) {
  return calculateTabRange(tab, today, state.weekOffset);
}

function tabLabel(tab) {
  return tab === "previous" ? "Previous" : tab === "next" ? "Next" : "Today";
}

function formatRange(range) {
  const from = dateFormatter.format(dateFromKey(range.from));
  return range.from === range.to ? from : `${from} – ${dateFormatter.format(dateFromKey(range.to))}`;
}

function matchSortKey(match) {
  return `${match.date || "9999-12-31"}T${match.time && match.time !== "—" ? match.time : "99:99"}`;
}

function sortMatches(matches) {
  return [...matches].sort((a, b) => matchSortKey(a).localeCompare(matchSortKey(b)));
}

function canonicalTeamCode(value) {
  const normalized = String(value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  return TEAM_CODE_ALIASES[normalized] || normalized.slice(0, 3);
}

function matchFallbackIdentity(match) {
  const codes = [match.blueCode || match.blue, match.redCode || match.red]
    .map(canonicalTeamCode)
    .sort();
  return JSON.stringify([
    match.date || "",
    codes,
    match.competition || match.league || "",
    match.time || "—"
  ]);
}

function matchIdentityKeys(match) {
  const keys = [];
  if (match.matchId) keys.push(`official:${String(match.matchId)}`);
  keys.push(`fallback:${matchFallbackIdentity(match)}`);
  return keys;
}

function recordQuality(match) {
  return [
    Boolean(match.matchId),
    match.blueScore != null && match.redScore != null,
    match.status === "completed",
    match.time != null && match.time !== "—",
    (match.gameIds || []).length,
    match.league === "LCK"
  ];
}

function compareQuality(left, right) {
  const a = recordQuality(left);
  const b = recordQuality(right);
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] !== b[index]) return a[index] ? 1 : -1;
  }
  return 0;
}

function mergeMatchRecord(existing, incoming) {
  const preferred = compareQuality(existing, incoming) >= 0 ? existing : incoming;
  const fallback = preferred === existing ? incoming : existing;
  const merged = { ...preferred };
  Object.entries(fallback).forEach(([key, value]) => {
    if (value == null || value === "" || value === "—"
      || (Array.isArray(value) && !value.length)) return;
    if (merged[key] == null || merged[key] === "" || merged[key] === "—"
      || (Array.isArray(merged[key]) && !merged[key].length)) {
      merged[key] = value;
    }
  });
  return merged;
}

function renderPeriodRange(range) {
  $("#period-range").textContent = formatRange(range);
  const browsingWeek = state.activeTab !== "today";
  $("#earlier-week").disabled = !browsingWeek || state.loadingBatch;
  $("#later-week").disabled = !browsingWeek || state.loadingBatch;
}

function renderTabs() {
  document.querySelectorAll(".match-tab").forEach((tab) => {
    const active = tab.dataset.tab === state.activeTab;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  $("#match-panel").setAttribute("aria-labelledby", `tab-${state.activeTab}`);
}

function renderMatches() {
  const range = tabRange(state.activeTab);
  const visible = sortMatches(state.matches.filter((match) => match.date >= range.from && match.date <= range.to && isTeamMatch(match, MATCH_TEAM)));
  $("#match-list").innerHTML = visible.map((match) => {
    const live = match.status === "live";
    const completed = match.status === "completed";
    return `<article class="match-card ${state.expandedMatchId === match.id ? "is-expanded" : ""}" data-match-key="${escapeHtml(match.id)}" tabindex="0" aria-expanded="${state.expandedMatchId === match.id}">
      <div class="match-meta"><time datetime="${escapeHtml(match.date)}">${escapeHtml(dateFormatter.format(dateFromKey(match.date)))}</time><strong>${escapeHtml(match.time)}</strong><span>${escapeHtml(match.league)}</span><span>${escapeHtml(match.stage)}</span></div>
      <div class="team-column team-one">${teamMarkup(match.blue, match.blueCode, match.blueLogo, match.blueScore, completed && match.blueScore < match.redScore)}</div>
      <div class="match-center"><span class="series">BEST OF ${escapeHtml(String(match.series || "BO3").replace("BO", ""))}</span><span class="match-series-score">${escapeHtml(`${match.blueScore ?? "—"} - ${match.redScore ?? "—"}`)}</span><strong class="versus">VS</strong><span class="status ${escapeHtml(match.status)}">${live ? "● Live" : escapeHtml(match.status)}</span>${match.link ? `<a class="source-link" href="${escapeHtml(match.link)}" target="_blank" rel="noreferrer">Leaguepedia ↗</a>` : ""}</div>
      <div class="team-column team-two">${teamMarkup(match.red, match.redCode, match.redLogo, match.redScore, completed && match.redScore < match.blueScore)}</div>
      ${state.expandedMatchId === match.id ? `<section class="match-details">${renderMatchDetails(match)}</section>` : ""}
    </article>`;
  }).join("");
  $("#match-heading").textContent = `${tabLabel(state.activeTab)} matches`;
  $("#empty-state").hidden = visible.length !== 0;
  if (!visible.length) {
    $("#empty-state").textContent = "No matches scheduled";
  }
  renderPeriodRange(range);
  updateBatchStatus();
  renderTabs();
}

function teamMarkup(name, code, logo, score, dimScore) {
  const logoSource = TEAM_LOGOS[code] || (logo ? `${API_BASE_URL}/api/logo?url=${encodeURIComponent(logo)}` : null);
  const logoMarkup = logoSource
    ? `<img class="team-logo" src="${escapeHtml(logoSource)}" alt="${escapeHtml(name)} logo" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.classList.add('is-visible')" /><span class="team-logo logo-fallback">${escapeHtml(code)}</span>`
    : `<span class="team-logo">${escapeHtml(code)}</span>`;
  return `${logoMarkup}<strong class="team-name">${escapeHtml(name)}</strong><span class="team-score ${dimScore ? "dim" : ""}">${escapeHtml(score ?? "—")}</span>`;
}

function isTeamMatch(match, team) {
  const wanted = canonicalTeamCode(team);
  return [match.blueCode, match.redCode, match.blue, match.red]
    .some((value) => canonicalTeamCode(value) === wanted);
}

function loadedScheduleRange() {
  if (!state.ranges.length) return null;
  return state.ranges.reduce((range, current) => ({
    from: current.from < range.from ? current.from : range.from,
    to: current.to > range.to ? current.to : range.to
  }));
}

function updateBatchStatus() {
  const loadedRange = loadedScheduleRange();
  const status = state.loadingBatch
    ? "Loading schedule…"
    : state.scheduleStale
      ? "Showing cached schedule · refresh when online"
      : loadedRange
        ? "Schedule ready"
        : "No schedule loaded";
  $("#updated").textContent = status;
}

function renderStandings() {
  const competition = state.standingsMeta.label || state.standingsMeta.league || "Current competition";
  $("#standings-heading").textContent = `${competition} leaderboard`;
  $("#standings-context").textContent = state.standingsMeta.stage
    ? `${state.standingsMeta.stage} · Series · Games`
    : "Series · Games";
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
  if (state.detailErrors.has(match.id)) return `<div class="detail-message"><p>${escapeHtml(state.detailErrors.get(match.id))}</p><button class="detail-retry" type="button">Retry</button></div>`;
  const details = state.details.get(match.id);
  if (!details) return `<p class="detail-message">Loading champion, item, and gold data…</p>`;
  const availableGames = (Array.isArray(details.games) ? details.games : []).filter((game) => game.available !== false);
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

function mergeMatches(matches) {
  const deduped = [];
  const indexes = new Map();
  matches.filter(Boolean).forEach((match) => {
    const existingIndex = matchIdentityKeys(match)
      .map((key) => indexes.get(key))
      .find((index) => index != null);
    const index = existingIndex == null ? deduped.length : existingIndex;
    deduped[index] = existingIndex == null
      ? match
      : mergeMatchRecord(deduped[index], match);
    matchIdentityKeys(deduped[index]).forEach((key) => indexes.set(key, index));
  });
  return sortMatches(deduped);
}

function normalizeRanges(ranges) {
  const ordered = ranges
    .filter((range) => range?.from && range?.to && range.from <= range.to)
    .map((range) => ({ from: range.from, to: range.to }))
    .sort((a, b) => a.from.localeCompare(b.from));
  return ordered.reduce((merged, range) => {
    const previous = merged[merged.length - 1];
    if (previous && shiftDate(previous.to, 1) >= range.from) {
      previous.to = previous.to > range.to ? previous.to : range.to;
    } else {
      merged.push(range);
    }
    return merged;
  }, []);
}

function saveBatches() {
  try {
    localStorage.setItem(BATCH_KEY, JSON.stringify({ version: 3, ranges: state.ranges, matches: state.matches }));
    localStorage.setItem(HISTORY_KEY, JSON.stringify(state.matches.slice(-500)));
  } catch (error) { console.warn("Schedule cache could not be saved.", error); }
}

function loadBatches() {
  try {
    LEGACY_BATCH_KEYS.forEach((key) => localStorage.removeItem(key));
    const saved = JSON.parse(localStorage.getItem(BATCH_KEY) || "null");
    if (saved?.matches) {
      state.matches = mergeMatches(saved.matches);
      state.ranges = normalizeRanges(saved.ranges || []);
    }
  } catch (error) { console.warn("Stored schedule cache could not be read.", error); }
}

function rangeContains(date) {
  return state.ranges.some((range) => range.from <= date && range.to >= date);
}

function rangeContainsInterval(from, to) {
  let cursor = from;
  for (const range of normalizeRanges(state.ranges)) {
    if (range.to < cursor) continue;
    if (range.from > cursor) return false;
    cursor = shiftDate(range.to, 1);
    if (cursor > to) return true;
  }
  return cursor > to;
}

async function fetchBatch(from, to) {
  if (state.loadingBatch || rangeContainsInterval(from, to)) return;
  state.loadingBatch = true; updateBatchStatus();
  try {
    const url = `${API_BASE_URL}/api/matches?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&team=${encodeURIComponent(MATCH_TEAM)}`;
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Schedule unavailable (${response.status})`);
    const payload = await response.json();
    state.matches = mergeMatches([
      ...state.matches.filter((match) => match.date < from || match.date > to),
      ...(payload.matches || [])
    ]);
    state.ranges = normalizeRanges([...state.ranges, { from, to }]).slice(-12);
    state.scheduleStale = false;
    saveBatches();
  } catch (error) {
    console.warn(error); state.scheduleStale = true;
  } finally {
    state.loadingBatch = false; renderMatches();
  }
}

async function loadStandings() {
  try {
    const response = await fetch(`${API_BASE_URL}/api/standings`);
    if (!response.ok) throw new Error(`Standings unavailable (${response.status})`);
    const payload = await response.json();
    state.standings = payload.standings || [];
    state.standingsMeta = payload.competition || { league: payload.league, season: payload.season };
  } catch (error) { console.warn(error); }
  renderStandings();
}

async function refreshMatches() {
  state.ranges = []; state.scheduleStale = false; state.weekOffset = 0;
  const today = dateKey(new Date());
  const previous = calculateTabRange("previous", today);
  const next = calculateTabRange("next", today);
  await fetchBatch(previous.from, next.to);
  loadStandings();
}

$("#match-tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".match-tab");
  if (!tab) return;
  state.activeTab = tab.dataset.tab;
  if (state.activeTab === "today") state.weekOffset = 0;
  renderMatches();
  const range = tabRange(state.activeTab);
  fetchBatch(range.from, range.to);
});
$("#match-tabs").addEventListener("keydown", (event) => {
  const tabs = [...document.querySelectorAll(".match-tab")];
  const currentIndex = tabs.findIndex((tab) => tab.dataset.tab === state.activeTab);
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const nextIndex = event.key === "Home" ? 0
    : event.key === "End" ? tabs.length - 1
      : (currentIndex + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  state.activeTab = tabs[nextIndex].dataset.tab;
  if (state.activeTab === "today") state.weekOffset = 0;
  renderMatches();
  tabs[nextIndex].focus();
  const range = tabRange(state.activeTab);
  fetchBatch(range.from, range.to);
});
$("#earlier-week").addEventListener("click", () => {
  if (state.activeTab === "today") return;
  state.weekOffset -= 1;
  state.expandedMatchId = null;
  renderMatches();
  const range = tabRange(state.activeTab);
  fetchBatch(range.from, range.to);
});
$("#later-week").addEventListener("click", () => {
  if (state.activeTab === "today") return;
  state.weekOffset += 1;
  state.expandedMatchId = null;
  renderMatches();
  const range = tabRange(state.activeTab);
  fetchBatch(range.from, range.to);
});
$("#refresh-button").addEventListener("click", refreshMatches);
$("#match-list").addEventListener("click", (event) => {
  const card = event.target.closest(".match-card");
  if (!card || event.target.closest("a")) return;
  const match = state.matches.find(({ id }) => id === card.dataset.matchKey);
  if (!match) return;
  if (event.target.closest(".detail-retry")) {
    state.detailErrors.delete(match.id);
    state.expandedMatchId = match.id;
    renderMatches();
    loadMatchDetails(match);
    return;
  }
  if (state.expandedMatchId === match.id) { state.expandedMatchId = null; renderMatches(); return; }
  state.expandedMatchId = match.id; renderMatches();
  if (match.matchId && !state.details.has(match.id) && !state.detailLoading.has(match.id)) loadMatchDetails(match);
});

loadBatches();
renderMatches();
renderStandings();
refreshMatches();
