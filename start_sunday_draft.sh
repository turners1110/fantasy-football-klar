#!/bin/bash
# One-command Sunday startup script.
# Enters the project directory, checks dependencies, verifies required
# files, runs a short state validation, then starts the live CLI.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Fantasy Auction Live Tool -- Sunday Startup ==="
echo "Project directory: $SCRIPT_DIR"

echo ""
echo "Checking Python..."
PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: python3 not found on PATH. Cannot start the live tool."
    echo "Fallback: open outputs/auction_rebuild/sunday_final/static_emergency_bid_sheet.csv directly."
    exit 1
fi
echo "  found: $PYTHON_BIN"

echo ""
echo "Checking required Python packages (pandas, numpy, pulp)..."
"$PYTHON_BIN" -c "import pandas, numpy, pulp" 2>/dev/null && echo "  OK" || {
    echo "WARNING: one or more required packages missing. The live tool may fail."
    echo "Fallback: use the static emergency sheet instead."
}

echo ""
echo "Checking required files..."
REQUIRED_FILES=(
    "data/keepers_2026_confirmed.csv"
    "live_auction_cli.py"
    "auction_engine/auction_state.py"
    "outputs/auction_rebuild/sunday_final/static_emergency_bid_sheet.csv"
)
MISSING=0
for f in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$f" ]; then
        echo "  MISSING: $f"
        MISSING=1
    fi
done
if [ "$MISSING" -eq 1 ]; then
    echo "ERROR: required file(s) missing. Use the emergency sheet instead of the live tool."
    exit 1
fi
echo "  all required files present."

echo ""
echo "Running a short state validation (builds the confirmed pre-draft state and checks legality)..."
"$PYTHON_BIN" -c "
import sys
sys.path.insert(0, '.')
from live_auction_cli import AuctionCLI
from auction_engine.auction_state_validation import validate
cli = AuctionCLI(log_path=None)
violations = validate(cli.store.state)
if violations:
    print('STATE VALIDATION FAILED:', violations)
    sys.exit(1)
sam = cli.store.state.teams['Sam']
print(f'State OK. Sam budget: \${sam.budget_remaining:.2f}  Keepers: {len(sam.roster)}')
" || {
    echo "ERROR: initial state failed validation. Do NOT trust the live tool -- use the emergency sheet."
    exit 1
}

echo ""
COMMIT_HASH="$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown (not a git checkout)')"
echo "Active commit: $COMMIT_HASH"
echo "Active market prior: STATIC_PRE_DRAFT_MARKET_PRIOR"
echo "  (the evolved market prior was tested and REJECTED in Stage 7 of the"
echo "   Sunday Final Build -- see outputs/auction_rebuild/sunday_final/final_report.md)"
echo ""
echo "Sam's confirmed keepers: Garrett Wilson WR \$31, Kenneth Walker III RB \$36,"
echo "  Quentin Johnston WR \$11, David Montgomery RB \$45, Cam Skattebo RB \$28,"
echo "  Jaxson Dart QB \$11. Keeper spend: \$162. Primary auction budget: \$223."
echo ""
echo "Emergency fallback at any time: type 'emergency' in the CLI, or open"
echo "  outputs/auction_rebuild/sunday_final/static_emergency_bid_sheet.csv directly."
echo ""
echo "Starting the live CLI now..."
echo ""
exec "$PYTHON_BIN" live_auction_cli.py
