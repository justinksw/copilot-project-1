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
assert.strictEqual(context.mergeMatches([
  match,
  { ...match, id: "different-stale-id", matchId: undefined }
]).length, 1);
assert.strictEqual(context.mergeMatches([
  match,
  { ...match, id: "rematch", matchId: "43", time: "16:00" }
]).length, 2);
assert.strictEqual(JSON.stringify(context.normalizeRanges([
  { from: "2026-08-01", to: "2026-08-03" },
  { from: "2026-08-04", to: "2026-08-07" },
  { from: "2026-08-10", to: "2026-08-12" }
])), JSON.stringify([
  { from: "2026-08-01", to: "2026-08-07" },
  { from: "2026-08-10", to: "2026-08-12" }
]));

console.log("frontend schedule regression tests passed");
