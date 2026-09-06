# Fantasy Football Auction System — Full Project Report

Generated 2026-09, current as of commit `5d7542d` (verified pushed and matching `origin/main`). Written for external review (ChatGPT or otherwise) — self-contained, no prior context assumed.

Repository: https://github.com/turners1110/fantasy-football-klar

---

## 1. What this project is, in one paragraph

A from-scratch decision-support system built for one team owner ("Sam") in a real, 12-team, $4,800-total ($400/team nominal, official per-team budgets vary) fantasy football auction league. It exists to answer, correctly and in real time during a live auction: what is this player actually worth to my specific roster right now, what will the room probably pay for him, what's the absolute most I should legally and rationally bid, and does my remaining plan for filling out a legal 16-man roster still work. It is not a general fantasy football tool — every rule, budget number, and keeper is this league's actual, real, commissioner-confirmed data.

## 2. The league, exactly as it actually works

- 12 teams, $4,800 combined starting budget (varies per team, see below — not a flat $400 for everyone)
- **16-player roster per team** (this took real correction effort — early work incorrectly assumed 15)
- Starting lineup: 1 QB, 2 RB, 2 WR, 1 TE, 3 FLEX (9 starters) + 7 bench = 16
- Half-PPR scoring, 4-point passing touchdowns
- Up to 6 veteran keepers per team, cost = prior salary + $10/year (or +$5/year if franchise-tagged, 1 tag per team)
- A separate "college-rights" system: teams can hold the rights to college players; when one enters the NFL, converting him to an active roster spot costs a flat $1, separate from the veteran auction budget, and does not count against the 6-keeper limit — but he still occupies one of the team's 16 roster slots
- League-wide: 79 total protected players (keepers + college-rights holds), 113 total veteran-auction openings, $3,066 total remaining auction cash

**Sam's specific state**: team name "Woody Johnson's D...efence" in commissioner records. 6 veteran keepers (Kenneth Walker III RB $36, David Montgomery RB $45, Garrett Wilson WR $31, Cam Skattebo RB $28, Jaxson Dart QB $11, Quentin Johnston WR $11 — total $162) + 2 college-rights holds (Fernando Mendoza QB, Isaiah Bond WR, $1 each, separate from auction budget) = 8 protected players, 8 open auction slots, **$225 live-auction budget**, **$218 initial legal maximum bid** (after reserving $1 for each of the other 7 open slots).

## 3. System architecture — what actually exists in the codebase

```
auction_model/        core valuation library: config/rules, keeper math, data loading,
                       calibration, anchor blending, exact roster solver (real MIP via HiGHS)
mock_draft/           a separate auction-mechanics simulator (nomination, bidding, sale-price
                       rules, opponent archetypes) -- NOT the evolutionary/genetic layer that
                       lives alongside it in the same directory, which was never run/validated
auction_engine/        event-sourced live auction state: append-only event log, undo/replay,
                       canonical player identity, dynamic roster-aware valuation, market-price
                       adjustment learning, live roster-path construction, practice-draft sessions
live_web/              the live website (FastAPI + vanilla JS) -- the PRIMARY interface for
                       the real draft: Draft Board, My Roster, Targets, Roster Paths, League
                       Room, Practice Draft, Log/Controls
live_auction_cli.py    a terminal fallback interface sharing the exact same backend/state as
                       the website (same event log, same functions) -- not a separate tool
run_live_web.py        launches the website; start_sunday_live_tool.sh is the recommended
                       startup script (handles safe resume/clean-start, LAN access, etc.)
draft_ui/              an OLDER, STALE website from earlier in the project -- explicitly
                       superseded, not used, kept only as historical reference
data/                  source-of-truth files: confirmed keepers, official team budgets,
                       college-rights holdings, historical salaries, protected-player overrides
outputs/auction_rebuild/  the full audit trail of every phase of this project's development
tests/                 pytest suite, 686 tests passing as of the last full run
```

## 4. Development history — the real journey, phase by phase

This was not built in one pass. It went through a long sequence of build-then-break-then-fix cycles, each triggered either by new real-world information (official commissioner data) or by adversarial review that found real bugs. Summarized:

**Early phase — the valuation model.** Built a from-scratch auction dollar-value engine (VBD-based, tiered, non-linear pricing) using FantasyPros projections and historical league salary data, iterated significantly after research into real auction-drafting theory (the initial linear model was unrealistic; budgets in real auctions concentrate heavily on stars, which the model didn't reflect until corrected).

**Keeper decision phase.** Used the valuation model to help Sam decide his 6 keepers, including resolving a real bug where a suffix mismatch ("Kenneth Walker III" vs "Kenneth Walker") silently zeroed his projection and mispriced him — fixed, which flipped him from a marginal cut to Sam's clear best keeper.

**Draft-day tooling phase 1.** Built `draft_ui/`, a first-generation live draft website. Superseded later — explicitly retired, not deleted, kept for history.

**"Phase 3" market-validation arc (3E → 3G).** A separate, adversarial audit of the whole valuation/simulation pipeline, specifically designed to NOT trust prior claims. Found and fixed: a broken CBC solver silently failing (switched to HiGHS), a circularity bug in calibration (a metric validating a model against a target derived from the model's own input), a genuine "dead parameter" bug where a correctly-computed roster-aware value wasn't actually wired into the bid recommendation, duplicate-player bugs (same person sold twice under different name spellings), a broken sale-price mechanic, and a meaningless "bidder count" statistic. Each fix was verified against real running processes, not just unit tests.

**Live Auction MVP → Website V1/V2/V2.1/V2.2.** Built the actual live interface: event-sourced state shared between website and CLI, a value-independence audit (proving market price can never inflate a player's value-to-Sam), a real dynamic roster-value engine (proven directly: buying RBs measurably crashes the marginal value of more RBs), a units bug (a player's projected *points* were being silently used as if they were *dollars* — fixed and reconfirmed multiple times as new code paths were added), LAN access, mobile/UI hardening, and a genuine (if scoped-down) practice-mode auction against simulated opponents.

**Official commissioner data arrived (V3 repair).** This was a big one: real league data revealed the roster size, purchase counts, and Sam's own budget were all being computed on wrong assumptions (15 vs 16 players, 108 vs 113 leaguewide auction slots, $223 vs $225 for Sam). A full migration was done, plus a canonical player-identity layer, real transaction-rule enforcement, concurrency/locking for simultaneous devices, and a from-scratch fix of the exact-solver's roster-slot math, which had been silently modeling an *impossible 18-player roster* for Sam (10 auction slots instead of 8, because college-rights occupancy wasn't being subtracted). All 7 formal release gates for this arc eventually passed.

**V3.1 — a second, sharper repair pass** (triggered by a code-level external review, not just testing) found and fixed 5 more concrete defects: the same 10-vs-8 slot bug recurring in a second code path (roster paths), a hardcoded stale bench-size constant (6 instead of 7), a real mislabeling bug where an *approximate* value was being shown to the user as if it were the *exact* solver-verified ceiling, practice-mode AI opponents not accounting for college-rights occupancy (causing incomplete practice drafts), and a real safety-critical **undo bug**: calling undo twice in a row didn't step back two events — the second undo *resurrected* the sale the first undo had just removed. All fixed and verified with real running-server tests (including literally killing a server process mid-draft and confirming recovery).

**Additional real bugs found during this repair, incidentally:** a sale-correction path that silently wiped a player's projected points and valuation metadata after correcting a price; a roster-cap check that ignored college-rights occupancy (could have let a team balloon past its real limit); a race-condition test proving two simultaneous bid submissions resolve safely (one wins, one is cleanly rejected, no duplicate sale); a genuine league-wide eligibility leak where 6 real NFL players were sellable in the live auction despite belonging to another team's held college-draft rights (found via a spreadsheet cross-reference, fixed with careful legacy-team-name resolution — 2 of the 8 originally-suspected cases were correctly left *unresolved* rather than guessed, since the data was genuinely ambiguous).

**Today's final fix — the underspend bug.** User-reported and independently reproduced: practice auctions were completing legally (113/113 sales, legal rosters) but Sam's simulated bidding was leaving 74-90% of his budget unspent, with a suspicious simultaneous collapse in three unrelated positions' marginal values partway through each draft. Root-caused precisely: the fallback dollar-conversion function was pricing a player's value-to-Sam using *that player's own generic market rate* instead of his actual marginal value to Sam's specific roster — meaning a great, still-affordable player could get priced down to $6-10 the moment Sam's nominal starting needs looked filled, even though he was worth 40+ points as a bench/FLEX upgrade. Fixed with a real budget/slots/alternatives-aware conversion. Verified: the specific flagged players' stops roughly doubled, and Sam's practice-draft spend rose from ~17% to ~50% of budget across 5 test seeds, with no new overpayment issue introduced.

## 5. What is genuinely solid right now (high confidence)

- **All roster/budget/eligibility mechanics.** 16-player structure, official per-team budgets, protected-player exclusion (keepers, college-rights, and now the cross-league college-draft-rights leak), legal-slot math, $1-reserve enforcement. Extensively tested against real commissioner data.
- **The exact solver** ("Run Exact" in the UI): a genuine mixed-integer-program solve (HiGHS) answering "is buying this player at price X better than my best alternative use of that money," using the real current roster/budget/pool. When it returns a number, that number is rigorous.
- **Event-sourcing/undo/replay/recovery**: verified with real process kills and restarts, not just in-memory tests.
- **The team-specific value fix from today**: a genuinely important correctness fix, verified with concrete before/after numbers on the exact players the bug was found on.
- **Website ↔ CLI parity**: both interfaces are provably reading/writing the same underlying state — confirmed via literal separate-process tests (start the website, make a sale, kill it, start the CLI, see the same sale).

## 6. Known limitations — the honest list

- **Expected market price (what the room will actually pay) is much less trustworthy than team-specific value.** Roughly 45% of players' simulated price distributions were found to be "degenerate" (flat, no real variance) in earlier validation — meaning the tool's guess at *what others will bid* is far shakier than its guess at *what a player is worth to Sam*. A direct backtest against real 2025 salary data (68 matched non-keeper players) showed only moderate correlation (0.515) between last year's real prices and this year's expected prices, with a systematic ~$15-17 downward bias (plausibly explained by this year's more keeper-depleted market, not necessarily an error).
- **The underspend fix is real but only partial.** Practice-draft spend rose from ~17% to ~45-56% of budget after today's fix — a large, verified improvement, but still below what a fully "spend it all rationally" policy should look like. The missing piece is a genuine "compare buying now vs. banking the cash for a better future opportunity, across the whole remaining draft" layer, which was explicitly deferred rather than rushed this close to the real auction.
- **Underlying football projections were never independently re-validated.** This entire engineering effort assumes the base FantasyPros-derived point projections are reasonable. No part of this work audited the projections themselves.
- **Two players' college-draft-rights ownership remains genuinely unresolved** (Josh Downs and CJ Stroud, both under a legacy team name "Paul" that doesn't map cleanly to any current team) — correctly excluded from the auction pool as a safety measure, but not attributed to a specific team, pending real commissioner confirmation.
- **The full practice-mode field of 11 AI opponents is a reasonable simulation, not a validated model of your actual league-mates' real behavior** — it's roster-aware and archetype-varied, but its realism relative to how your specific league actually bids has never been (and can't be, until tonight) checked against real outcomes.
- **Credible-bidder-count logic and nomination-order diversity** were defined and fixed at a mechanical level but, like the market-price point above, are inherently unverifiable against real human behavior until used live.
- **Tier-specific market learning is real but shallow** — the mechanism now correctly uses each player's real tier (a hardcoded placeholder bug was found and fixed today), but hasn't been stress-tested across a full draft's worth of real tier-differentiated price movement.
- **Monte Carlo / simulation-based price distributions exist but are explicitly NOT promoted to the live board** unless a batch passes a strict legitimacy gate — currently, no batch has passed that gate, so the live tool correctly falls back to the simpler, more-tested static/shrinkage-based price model rather than a richer but unvalidated simulated one.

## 7. My own honest assessment

This system earned its current level of trust the hard way — nearly every review pass, whether triggered by new data, external code review, or a user just trying the practice mode, found at least one real, previously-invisible bug. That's not a reason to distrust it more; if anything, the fact that so many issues were found *and each one held up under independent re-verification* (real running-server tests, not just "the code looks right now") is the best evidence available that what remains has been through real scrutiny. But it does mean I'd calibrate confidence per-component rather than treat the system as uniformly reliable:

- **Trust it fully** for: is this roster legal, what's my real budget/reserve situation, what's the rigorous exact ceiling when I click Run Exact.
- **Trust it directionally** for: the fast recommended stop, whether a player is a good target at all.
- **Treat as a rough guess, sanity-check yourself** for: exactly what a player will sell for, whether to wait for a better option later in the draft.

If I had another day rather than three hours, the two things I'd want to do next are (1) finish the full-roster-completion decision layer that the underspend fix explicitly left for later, and (2) get real commissioner confirmation on the two unresolved "Paul" college-rights players. Neither is a blocker for tonight — the system fails safe in both cases (conservative rather than reckless underspend; exclusion rather than incorrect inclusion) — but both are real, known gaps, not hidden ones.

## 8. Test and verification status

686 tests passing, 0 failed, 15 skipped, as of commit `5d7542d`. Every major fix in this report was verified two ways: the automated test suite, AND a real end-to-end check against an actually-running server process (curl/HTTP requests, real practice drafts run to completion, literal process kills to test recovery) — this dual standard was maintained deliberately throughout, specifically because "the tests pass" was found, more than once, to not be sufficient evidence on its own.

## 9. How to actually use it

```
./start_sunday_live_tool.sh              # local-only, no token
./start_sunday_live_tool.sh --lan        # LAN access, prints a security token
./start_sunday_live_tool.sh --lan --no-auth   # LAN access, no token (trusted network only)
```
Then open `http://127.0.0.1:8010` (or the printed LAN URL from a second device). Fallback: `python3 live_auction_cli.py` (same backend, terminal interface). Emergency: the "Emergency" button/command prints a fully static backup bid sheet if the live tool becomes unavailable mid-draft.

## 10. What would be useful from external review

- A second opinion on whether the remaining underspend gap (~50% budget usage) is an acceptable risk for tonight's real auction, or whether it changes the practical guidance Sam should follow (e.g., "trust the tool's picks but manually watch your own unspent cash as the draft winds down").
- Any structural concern with how "expected market price" and "team-specific value" are kept separate — this project treats conflating them as the single most dangerous class of bug (it recurred in different forms multiple times), so a fresh set of eyes checking that boundary is still intact would be valuable.
- Whether the two unresolved college-rights players (Josh Downs, CJ Stroud under legacy owner "Paul") represent any residual risk beyond what's already mitigated by excluding them outright.
