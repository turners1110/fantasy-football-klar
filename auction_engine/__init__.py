"""Phase 4 Stage 1: canonical event-sourced auction state.

This package is the single source of truth for live-auction state. The
website (draft_ui/), the simulator (mock_draft/), and the exact solver
(auction_model/exact_roster_solver.py) must all read state FROM this
package rather than maintaining their own copies -- Stage 1's explicit
requirement ("Do not maintain separate state logic in the website,
simulator, and exact solver"). Wiring draft_ui/ to actually call into
this package is Stage 6 work and is NOT done as part of Stage 1 itself
(see phase4_final_report.md for the honest scope line between "this
package exists and is tested" and "the website uses it").
"""
