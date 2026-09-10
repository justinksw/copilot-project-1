const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(`${__dirname}/app.js`, "utf8");
const executableSource = source.slice(0, source.indexOf('$("#match-tabs").addEventListener'));
const context = {
  console,
  Intl,
  Map,
  Set,
  URL,
  URLSearchParams,
  AbortController,
  AbortSignal,
  setTimeout,
  clearTimeout,
  fetch: async () => { throw new Error("fetch should be mocked in timeout tests"); },
  window: { NEXUS_API_BASE_URL: "", location: { hostname: "localhost" } },
  document: { querySelector: () => null }
};
vm.createContext(context);
vm.runInContext(executableSource, context);

const match = {
  id: "stale-id",
  matchId: "42",
  date: "2026-08-30",
  time: "12:00",
  league: "LCK",
  competition: "LCK",
  blue: "T1",
  red: "Gen.G",
  blueCode: "T1",
  redCode: "GEN",
  blueScore: 2,
  redScore: 1,
  status: "completed"
};

assert.strictEqual(context.canonicalTeamCode("Gen.G"), "GEN");
assert.strictEqual(context.isTeamMatch(match, "T1"), true);
assert.strictEqual(context.matchStatus({ status: "upcoming", blueScore: 2, redScore: 3 }), "completed");
assert.strictEqual(context.matchStatus({ status: "live", blueScore: null, redScore: null }), "live");
assert.strictEqual(context.matchStatus({
  status: "completed", blue: "T1", red: "TBD", blueScore: 8, redScore: 0
}), "upcoming");
assert.strictEqual(context.matchStatus({
  status: "completed", blueCode: "WIN", blue: "Winner of semifinal 1",
  red: "T1", blueScore: 2, redScore: 1
}), "upcoming");
assert.strictEqual(context.validMatchScore({
  blue: "T1", red: "Gen.G", blueScore: 2, redScore: 1
}), true);
assert.strictEqual(JSON.stringify(context.calculateTabRange("previous", "2026-09-03")), JSON.stringify({
  from: "2026-08-24",
  to: "2026-09-02"
}));
assert.strictEqual(JSON.stringify(context.calculateTabRange("today", "2026-09-03")), JSON.stringify({
  from: "2026-09-03",
  to: "2026-09-03"
}));
assert.strictEqual(JSON.stringify(context.calculateTabRange("next", "2026-09-03")), JSON.stringify({
  from: "2026-09-04",
  to: "2026-09-13"
}));
assert.strictEqual(JSON.stringify(context.calculateTabRange("previous", "2026-09-03", -1)), JSON.stringify({
  from: "2026-08-17",
  to: "2026-08-26"
}));
assert.strictEqual(JSON.stringify(context.calculateTabRange("next", "2026-09-03", 1)), JSON.stringify({
  from: "2026-09-11",
  to: "2026-09-20"
}));
assert.strictEqual(JSON.stringify(context.initialBatchRange("2026-09-03")), JSON.stringify({
  from: "2026-09-02",
  to: "2026-09-04"
}));
assert.strictEqual(context.mergeMatches([
  match,
  { ...match, id: "different-stale-id", matchId: undefined }
]).length, 1);
assert.match(
  context.matchDetailsUrl({
    matchId: "42",
    gameIds: [{ id: "1001", state: "completed" }],
    startTime: "2026-08-30T04:00:00Z",
    teamIds: { "team:1": "T1" }
  }, true),
  /gameIds=.*1001.*&startTime=.*&teamIds=.*&refresh=1$/
);
assert.match(
  context.gameDetailMarkup(match, {
    number: 1, available: false,
    message: "The official live-stats feed is unavailable."
  }),
  /The official live-stats feed is unavailable\./
);
assert.strictEqual(context.mergeMatches([
  { ...match, matchId: undefined },
  match
])[0].matchId, "42");
assert.strictEqual(context.mergeMatches([
  match,
  { ...match, id: "rematch", matchId: "43", time: "16:00" }
]).length, 2);
assert.strictEqual(context.mergeMatches([
  match,
  { ...match, id: "history-copy", matchId: undefined, competition: "T1" }
]).length, 1);
assert.strictEqual(context.mergeMatches([
  { ...match, blue: "T1", red: "TBD", blueScore: 8, redScore: 0, status: "completed" },
  { ...match, id: "schedule-copy", blue: "T1", red: "TBD", blueScore: null, redScore: null, status: "upcoming" }
])[0].status, "upcoming");
assert.strictEqual(JSON.stringify(context.normalizeRanges([
  { from: "2026-08-01", to: "2026-08-03" },
  { from: "2026-08-04", to: "2026-08-07" },
  { from: "2026-08-10", to: "2026-08-12" }
])), JSON.stringify([
  { from: "2026-08-01", to: "2026-08-07" },
  { from: "2026-08-10", to: "2026-08-12" }
]));
assert.strictEqual(JSON.stringify(context.uncoveredRanges("2026-08-01", "2026-08-12", [
  { from: "2026-08-01", to: "2026-08-07" },
  { from: "2026-08-10", to: "2026-08-12" }
])), JSON.stringify([
  { from: "2026-08-08", to: "2026-08-09" }
]));
assert.strictEqual(context.FETCH_TIMEOUT_MS, 18000);
assert.strictEqual(typeof context.fetchWithTimeout, "function");
assert.strictEqual(typeof context.fetchJson, "function");
assert.match(source, /AbortController/);
assert.match(source, /Refreshing schedule…/);
assert.match(source, /scheduleError/);

(async () => {
  let aborted = false;
  const originalFetch = context.fetch;
  context.fetch = (url, options = {}) => new Promise((resolve, reject) => {
    if (options.signal) {
      if (options.signal.aborted) {
        aborted = true;
        const error = new Error("The operation was aborted");
        error.name = "AbortError";
        reject(error);
        return;
      }
      options.signal.addEventListener("abort", () => {
        aborted = true;
        const error = new Error("The operation was aborted");
        error.name = "AbortError";
        reject(error);
      });
    }
  });
  await assert.rejects(
    () => context.fetchWithTimeout("https://example.test/slow", {}, 25),
    (error) => error && error.name === "AbortError"
  );
  assert.strictEqual(aborted, true);
  context.fetch = async () => ({
    ok: false,
    status: 504,
    json: async () => ({ error: "Schedule request timed out." })
  });
  await assert.rejects(
    () => context.fetchJson("https://example.test/error"),
    /Schedule request timed out\./
  );
  let preservedSignal = null;
  const callerController = new AbortController();
  context.fetch = (url, options = {}) => {
    preservedSignal = options.signal;
    return new Promise((resolve, reject) => {
      options.signal.addEventListener("abort", () => {
        const error = new Error("The operation was aborted");
        error.name = "AbortError";
        reject(error);
      }, { once: true });
      callerController.abort();
    });
  };
  await assert.rejects(
    () => context.fetchWithTimeout("https://example.test/cancelled", { signal: callerController.signal }, 1000),
    (error) => error && error.name === "AbortError"
  );
  assert.ok(preservedSignal);
  assert.strictEqual(preservedSignal.aborted, true);
  context.fetch = originalFetch;
  console.log("frontend schedule regression tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
