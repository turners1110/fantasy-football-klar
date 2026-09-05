#!/usr/bin/env bash
# V2.1 Part 11 -- Sunday startup script.
#
# Real behavior (not just described): if the production session log
# already contains events (i.e. a prior run recorded sales and then
# stopped -- crash, ctrl-c, laptop closed, whatever), this script
# refuses to silently wipe it. It reports how many events are in the
# log and asks Sam to choose:
#   [r]esume -- replay the existing log and keep going from there
#   [c]lean  -- archive the old log and start a brand-new session
#   [e]xit   -- do nothing, don't launch anything
#
# Non-interactive use (tests, automation): pass --mode=resume,
# --mode=clean, or --mode=exit to skip the prompt.
set -euo pipefail

cd "$(dirname "$0")"

LOG_PATH="outputs/auction_rebuild/live_mvp/cli_session.jsonl"
MODE=""
HOST="127.0.0.1"

for arg in "$@"; do
  case "$arg" in
    --mode=resume) MODE="resume" ;;
    --mode=clean)  MODE="clean" ;;
    --mode=exit)   MODE="exit" ;;
    --mode=*) echo "Unknown --mode value in $arg (expected resume|clean|exit)" >&2; exit 2 ;;
    --lan) HOST="0.0.0.0" ;;
  esac
done

if [ "$HOST" = "0.0.0.0" ]; then
  LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")
  echo "=============================================================="
  echo " LAN ACCESS ENABLED (--lan): binding to 0.0.0.0:8010."
  if [ -n "$LAN_IP" ]; then
    echo " On another device on the SAME WiFi, open: http://$LAN_IP:8010"
  else
    echo " Could not auto-detect a LAN IP. Find it yourself with:"
    echo "   ipconfig getifaddr en0   (or check System Settings > Network)"
    echo " Then use http://<that-ip>:8010 on the other device."
  fi
  echo " If macOS prompts to allow incoming network connections for"
  echo " Python, click Allow -- otherwise other devices cannot reach it"
  echo " (System Settings > Network > Firewall > Options if you need to"
  echo " check/change it after the fact)."
  echo "=============================================================="
fi

EVENT_COUNT=0
if [ -f "$LOG_PATH" ]; then
  EVENT_COUNT=$(grep -c . "$LOG_PATH" 2>/dev/null || true)
  EVENT_COUNT=${EVENT_COUNT:-0}
fi

if [ "$EVENT_COUNT" -gt 0 ]; then
  echo "=============================================================="
  echo " WARNING: an existing production session log was found:"
  echo "   $LOG_PATH"
  echo "   ($EVENT_COUNT recorded event(s) -- this looks like a draft"
  echo "    that was already in progress.)"
  echo "=============================================================="
  if [ -z "$MODE" ]; then
    if [ -t 0 ]; then
      read -r -p "Resume the in-progress draft, start clean, or exit? [r/c/e]: " ANSWER
      case "$ANSWER" in
        r|R|resume) MODE="resume" ;;
        c|C|clean)  MODE="clean" ;;
        *)          MODE="exit" ;;
      esac
    else
      echo "No TTY available to prompt and no --mode given -- refusing to guess. Exiting." >&2
      exit 1
    fi
  fi
else
  # Nothing to resume -- clean and resume behave identically (fresh start).
  MODE=${MODE:-clean}
fi

case "$MODE" in
  exit)
    echo "Exiting without starting the server. Production state left untouched."
    exit 0
    ;;
  resume)
    echo "Resuming existing session ($EVENT_COUNT event(s))."
    export AUCTION_RESUME_MODE="resume"
    ;;
  clean)
    if [ "$EVENT_COUNT" -gt 0 ]; then
      ARCHIVE="outputs/auction_rebuild/live_mvp/cli_session_archived_$(date +%Y%m%d_%H%M%S).jsonl"
      cp "$LOG_PATH" "$ARCHIVE"
      echo "Archived old session log to $ARCHIVE before starting clean."
    fi
    echo "Starting a clean session."
    export AUCTION_RESUME_MODE="clean"
    ;;
esac

echo "Launching Sunday Live Auction Tool on http://$HOST:8010 ..."
exec python3 run_live_web.py --host "$HOST"
