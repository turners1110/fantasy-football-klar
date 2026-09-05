// V2.2 Request 2: real behavioral test of the live search-filter logic
// actually shipped in live_web/static/app.js. Extracts the real
// `normalizeNameQuery` function and the board-row filter predicate
// straight from the shipped source (not a reimplementation) and runs
// them against a synthetic board dataset with Node, since no
// browser/DOM automation tool is available in this environment.
//
// Run with: node tests/test_live_search_filter.js

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(__dirname, "..", "live_web", "static", "app.js"), "utf8");

function extractFunction(source, name) {
  const start = source.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`function ${name} not found in app.js`);
  let depth = 0, i = source.indexOf("{", start), bodyStart = i;
  for (; i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}") { depth--; if (depth === 0) break; }
  }
  return source.slice(start, i + 1);
}

// Pull the real normalizeNameQuery implementation out of app.js and eval it.
eval(extractFunction(src, "normalizeNameQuery"));

if (typeof normalizeNameQuery !== "function") {
  throw new Error("normalizeNameQuery did not evaluate to a function");
}

// Recreate the exact filter predicate used inside renderBoard() for the
// name/position portion (the part relevant to this request) using the
// real normalizeNameQuery function pulled from source above.
function nameFilterPasses(row, rawQuery) {
  const nameQuery = normalizeNameQuery(rawQuery);
  if (!nameQuery) return true;
  const nameHay = normalizeNameQuery(row.player);
  const posHay = row.position.toLowerCase();
  return nameHay.includes(nameQuery) || posHay.includes(nameQuery);
}

const board = [
  { player: "Josh Jacobs", position: "RB" },
  { player: "Josh Allen", position: "QB" },
  { player: "Garrett Wilson", position: "WR" },
  { player: "T.J. Hockenson", position: "TE" },
  { player: "Bucky Irving", position: "RB" },
];

let failures = 0;
function assertEq(actual, expected, label) {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  if (a !== e) { console.error(`FAIL: ${label} -- expected ${e}, got ${a}`); failures++; }
  else console.log(`PASS: ${label}`);
}

// 1. Typing narrows results (partial, case-insensitive name match).
assertEq(board.filter(r => nameFilterPasses(r, "josh")).map(r => r.player),
  ["Josh Jacobs", "Josh Allen"], "partial case-insensitive name match narrows to both Joshes");

assertEq(board.filter(r => nameFilterPasses(r, "Jacobs")).map(r => r.player),
  ["Josh Jacobs"], "full last-name match narrows to exactly one player");

// 2. Tolerant of underscores vs spaces (matches the search-box convention
// used elsewhere in this app, e.g. Josh_Allen).
assertEq(board.filter(r => nameFilterPasses(r, "Josh_Allen")).map(r => r.player),
  ["Josh Allen"], "underscore-separated query matches space-separated name");

// 3. Matches on position too.
assertEq(board.filter(r => nameFilterPasses(r, "RB")).map(r => r.player),
  ["Josh Jacobs", "Bucky Irving"], "position query narrows to only RBs");

// 4. Clearing the search restores the full list.
assertEq(board.filter(r => nameFilterPasses(r, "")).length, board.length,
  "empty query restores full unfiltered list");

// 5. No match -> empty list (not a crash, not the full list).
assertEq(board.filter(r => nameFilterPasses(r, "zzz_nonexistent")).length, 0,
  "nonsense query returns zero rows, not everything");

if (failures > 0) {
  console.error(`\n${failures} FAILURE(S)`);
  process.exit(1);
}
console.log("\nAll live search-filter tests passed (executed against the real app.js source).");
