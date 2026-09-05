"""V3 Part 4 -- website and CLI must default to the SAME production
event log. Before this fix, live_web/server.py hardcoded its own
"web_session.jsonl" while live_auction_cli.py's terminal REPL defaulted
to "cli_session.jsonl" -- two different production histories for the
same live draft, silently divergent if Sam ever fell back from the
website to the terminal CLI mid-draft."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from live_auction_cli import DEFAULT_LOG_PATH
import live_web.server as server_module


def test_website_and_cli_share_the_same_default_log_path():
    assert server_module.cli.log_path == DEFAULT_LOG_PATH


def test_website_module_does_not_hardcode_a_separate_web_session_path():
    assert "web_session" not in str(server_module.cli.log_path)
