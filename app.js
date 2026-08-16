const TIME_ZONE = "Asia/Hong_Kong";
const DEFAULT_STAGE_PAGE = "https://lol.fandom.com/wiki/LCK/2026_Season/Rounds_3-4";
const HISTORY_KEY = "nexus-watch-match-history-v1";
const TEAM_LOGOS = Object.freeze({
  BFX: "/assets/team-logos/BFX.png",
  BRO: "/assets/team-logos/BRO.png",
  DK: "/assets/team-logos/DK.png",
  DNS: "/assets/team-logos/DNS.png",
  GEN: "/assets/team-logos/GEN.png",
  HLE: "/assets/team-logos/HLE.png",
  KRX: "/assets/team-logos/KRX.png",
  KT: "/assets/team-logos/KT.png",
  NS: "/assets/team-logos/NS.png",
  T1: "/assets/team-logos/T1.png"
});
const state = {
  dateOffset: 0,
  matches: [],
  expandedMatchId: null,
  details: new Map(),
  detailLoading: new Set(),
  detailErrors: new Map()
};

const fallbackMatches = [
  { date: "2026-08-16", time: "16:00", status: "completed", league: "LCK", stage: "Rounds 3-4", blue: "T1", red: "GEN", blueCode: "T1", redCode: "GEN", blueScore: 0, redScore: 2, series: "BO3", link: DEFAULT_STAGE_PAGE },
  { date: "2026-08-16", time: "18:00", status: "completed", league: "LCK", stage: "Rounds 3-4", blue: "KRX", red: "BRO", blueCode: "KRX", redCode: "BRO", blueScore: 1, redScore: 2, series: "BO3", link: DEFAULT_STAGE_PAGE }
];

const $ = (selector) => document.querySelector(selector);
const dateFormatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: TIME_ZONE });
const timeFormatter = new Intl.DateTimeFormat("en-HK", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: TIME_ZONE });
const dateKey = (date) => new Intl.DateTimeFormat("en-CA", { year: "numeric", month: "2-digit", day: "2-digit", timeZone: TIME_ZONE }).format(date);
const selectedDate = () => dateKey(new Date(Date.now() + state.dateOffset * 86400000));
const formattedDate = () => dateFormatter.format(new Date(Date.now() + state.dateOffset * 86400000));
const displayTime = (date) => timeFormatter.format(new Date(date));

function renderMatches() {
  const visible = state.matches.filter((match) =>
    match.date === selectedDate()
  );
  $("#match-list").innerHTML = visible.map((match) => {
    const live = match.status === "live";
    const completed = match.status === "completed";
    return `<article class="match-card ${state.expandedMatchId === match.id ? "is-expanded" : ""}" data-match-key="${match.id}" tabindex="0" aria-expanded="${state.expandedMatchId === match.id}">
      <div class="match-meta"><strong>${match.time}</strong><span>${match.league}</span><span>${match.stage}</span></div>
      <div class="team-column team-one">${teamMarkup(match.blue, match.blueCode, match.blueLogo, match.blueScore, completed && match.blueScore < match.redScore)}</div>
      <div class="match-center"><span class="series">BEST OF ${match.series.replace("BO", "")}</span><span class="match-series-score">${match.blueScore ?? "—"} - ${match.redScore ?? "—"}</span><strong class="versus">VS</strong><span class="status ${match.status}">${live ? "● Live" : match.status}</span>${match.link ? `<a class="source-link" href="${match.link}" target="_blank" rel="noreferrer">Leaguepedia ↗</a>` : ""}</div>
      <div class="team-column team-two">${teamMarkup(match.red, match.redCode, match.redLogo, match.redScore, completed && match.redScore < match.blueScore)}</div>
      ${state.expandedMatchId === match.id ? `<section class="match-details">${renderMatchDetails(match)}</section>` : ""}
    </article>`;
  }).join("");
  $("#empty-state").hidden = visible.length !== 0;
  if (!visible.length) {
    const dates = [...new Set(state.matches.map(({ date }) => date))].sort();
    const nextDate = dates.find((date) => date > selectedDate());
    const previousDate = [...dates].reverse().find((date) => date < selectedDate());
    const adjacent = nextDate
      ? `Next LCK match day: ${dateFormatter.format(new Date(`${nextDate}T00:00:00+08:00`))}.`
      : previousDate
        ? `Previous LCK match day: ${dateFormatter.format(new Date(`${previousDate}T00:00:00+08:00`))}.`
        : "Try refreshing the schedule.";
    $("#empty-state").textContent = `No LCK matches scheduled for ${formattedDate()}. ${adjacent}`;
  }

  function teamMarkup(name, code, logo, score, dimScore) {
    const logoSource = TEAM_LOGOS[code] || (logo ? `/api/logo?url=${encodeURIComponent(logo)}` : null);
    const logoMarkup = logoSource
      ? `<img class="team-logo" src="${logoSource}" alt="${name} logo" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.classList.add('is-visible')" /><span class="team-logo logo-fallback">${code}</span>`
      : `<span class="team-logo">${code}</span>`;
    return `${logoMarkup}<strong class="team-name">${name}</strong><span class="team-score ${dimScore ? "dim" : ""}">${score ?? "—"}</span>`;
  }
}

function renderMatchDetails(match) {
  if (!match.matchId) {
    return `<p class="detail-message">Detailed game data is not linked for this match.</p>`;
  }
  if (state.detailLoading.has(match.id)) {
    return `<p class="detail-message">Loading champion, item, and gold data…</p>`;
  }
  if (state.detailErrors.has(match.id)) {
    return `<p class="detail-message">${state.detailErrors.get(match.id)}</p>`;
  }
  const details = state.details.get(match.id);
  if (!details) {
    return `<p class="detail-message">Loading champion, item, and gold data…</p>`;
  }
  const availableGames = details.games.filter((game) => game.available !== false);
  return availableGames.length
    ? availableGames.map((game) => gameDetailMarkup(match, game)).join("")
    : `<p class="detail-message">No game details are available yet.</p>`;
}

function gameDetailMarkup(match, game) {
  const patch = game.patch || "16.15.1";
  const teams = [game.teams.blue, game.teams.red];
  const winner = game.winner ? teamName(match, game.winner) : null;
  return `<section class="game-detail">
    <div class="game-detail-heading"><strong>Game ${game.number}</strong>${winner ? `<span class="game-winner">${winner} won</span>` : ""}<span>${game.state}</span><span>Patch ${patch}</span></div>
    <div class="game-detail-summary">
      ${teams.map((team) => `<div class="detail-team-summary"><strong>${teamName(match, team.code)}</strong><span>${formatGold(team.totalGold)} gold · ${team.kills} kills</span></div>`).join('<span class="detail-divider">VS</span>')}
    </div>
    <div class="player-columns">
      ${teams.map((team, teamIndex) => {
        const opponent = teams[teamIndex === 0 ? 1 : 0];
        return `<div class="player-column"><h4>${teamName(match, team.code)}</h4>${team.participants.map((player) => {
          const opponentPlayer = opponent.participants.find((candidate) => candidate.role === player.role);
          return participantMarkup(player, patch, opponentPlayer);
        }).join("")}</div>`;
      }).join("")}
    </div>
  </section>`;
}

function participantMarkup(player, patch, opponent) {
  const champion = player.champion
    ? `<img class="champion-icon" src="https://ddragon.leagueoflegends.com/cdn/${patch}/img/champion/${encodeURIComponent(player.champion)}.png" alt="" loading="lazy" />`
    : `<span class="champion-icon champion-placeholder">?</span>`;
  const items = player.items.length
    ? player.items.map((item) => `<img class="item-icon" src="https://ddragon.leagueoflegends.com/cdn/${patch}/img/item/${item}.png" alt="" loading="lazy" />`).join("")
    : '<span class="items-empty">—</span>';
  const goldDifference = player.gold - (opponent?.gold ?? player.gold);
  const csDifference = player.cs - (opponent?.cs ?? player.cs);
  return `<div class="player-row">
    <div class="player-identity">${champion}<span><strong>${player.player}</strong><small>${player.role || "player"}</small></span></div>
    <span class="player-kda">${player.kills}/${player.deaths}/${player.assists}</span>
    <span class="player-economy"><span class="player-gold">${formatGold(player.gold)} <span class="stat-difference ${differenceClass(goldDifference)}">(${formatDifference(goldDifference)})</span></span><small>${player.cs} CS <span class="stat-difference ${differenceClass(csDifference)}">(${formatDifference(csDifference)})</span></small></span>
    <div class="item-list">${items}</div>
  </div>`;
}

function formatGold(value) {
  return Number(value || 0).toLocaleString();
}

function formatDifference(value) {
  return `${value > 0 ? "+" : ""}${Number(value || 0).toLocaleString()}`;
}

function differenceClass(value) {
  return value > 0 ? "positive" : value < 0 ? "negative" : "even";
}

function teamName(match, code) {
  return code === match.blueCode ? match.blue : code === match.redCode ? match.red : code;
}

async function loadMatchDetails(match) {
  state.detailLoading.add(match.id);
  renderMatches();
  try {
    const response = await fetch(`/api/match-details?matchId=${encodeURIComponent(match.matchId)}`);
    if (!response.ok) throw new Error(`Match details unavailable (${response.status})`);
    state.details.set(match.id, await response.json());
  } catch (error) {
    console.warn(error);
    state.detailErrors.set(match.id, "Match details are currently unavailable.");
  } finally {
    state.detailLoading.delete(match.id);
    if (state.expandedMatchId === match.id) renderMatches();
  }
}

function updateDate() {
  $("#date-value").textContent = formattedDate();
  $("#date-label").textContent = state.dateOffset === 0 ? "Today" : state.dateOffset < 0 ? "Previous day" : "Next day";
  $("#match-heading").textContent = state.dateOffset === 0 ? "Today's matches" : `Matches · ${formattedDate()}`;
}

async function refreshMatches() {
  $("#updated").textContent = "Refreshing…";
  const proxyResult = await Promise.allSettled([fetchProxyMatches()]);
  const fetched = proxyResult[0].status === "fulfilled" ? proxyResult[0].value : [];
  const cached = loadHistory();
  state.matches = mergeMatches([...cached, ...fetched]);
  saveHistory(state.matches);
  if (!fetched.length) console.warn("Leaguepedia is unavailable; showing cached or verified data.");
  $("#updated").textContent = fetched.length
    ? `Updated ${timeFormatter.format(new Date())} HKT`
    : "Leaguepedia unavailable · showing cached data";
  renderMatches();
}

function mergeMatches(matches) {
  const verifiedDates = new Set(fallbackMatches.map(({ date }) => date));
  const deduped = new Map();
  fallbackMatches.forEach((match) => deduped.set(`${match.date}-${match.blue}-${match.red}`, match));
  matches.filter(Boolean).forEach((match) => {
    if (match.league === "LCK" && verifiedDates.has(match.date) && !match.link) return;
    const key = `${match.date}-${match.blue}-${match.red}`;
    deduped.set(key, match);
  });
  return [...deduped.values()].sort((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`));
}

function loadHistory() {
  try {
    return JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  } catch (error) {
    console.warn("Stored match history could not be read.", error);
    return [];
  }
}

function saveHistory(matches) {
  try {
    localStorage.setItem(HISTORY_KEY, JSON.stringify(matches.slice(-500)));
  } catch (error) {
    console.warn("Match history could not be saved.", error);
  }
}

async function fetchProxyMatches() {
  const response = await fetch("/api/matches");
  if (!response.ok) throw new Error(`Local schedule proxy failed (${response.status})`);
  const payload = await response.json();
  if (payload?.stage?.label) {
    $("#hero-stage").textContent = `${payload.stage.year} ${payload.stage.label.replace("Season ", "")}`;
  }
  return payload?.matches ?? [];
}

function moveToMatchDay(direction) {
  const current = selectedDate();
  const dates = [...new Set(state.matches.map(({ date }) => date))].sort();
  const target = direction < 0
    ? [...dates].reverse().find((date) => date < current)
    : dates.find((date) => date > current);
  if (!target) return;
  const delta = (Date.parse(`${target}T00:00:00+08:00`) - Date.parse(`${current}T00:00:00+08:00`)) / 86400000;
  state.dateOffset += delta;
  updateDate();
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
  if (state.expandedMatchId === match.id) {
    state.expandedMatchId = null;
    renderMatches();
    return;
  }
  state.expandedMatchId = match.id;
  renderMatches();
  if (!state.details.has(match.id) && !state.detailLoading.has(match.id)) loadMatchDetails(match);
});

state.matches = fallbackMatches.map((match) => ({ ...match }));
updateDate();
renderMatches();
refreshMatches();
