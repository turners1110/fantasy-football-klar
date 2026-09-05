"""V3 Part 4 -- website and CLI must default to the SAME production
event log. Before this fix, live_web/server.py hardcoded its own
"web_session.jsonl" while live_auction_cli.py's terminal REPL defaulted
to "cli_session.jsonl" -- two different production histories for the
same live draft, silently divergent if Sam ever fell back from the
website to the terminal CLI mid-draft."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import live_auction_cli
from live_auction_cli import DEFAULT_LOG_PATH


def _fresh_server_module():
    # test_live_web.py's `client` fixture monkeypatches
    # live_auction_cli.DEFAULT_LOG_PATH and reloads live_web.server in
    # place (mutating the SAME module object in sys.modules), which can
    # leak a scratch-path `cli` into later tests if this file runs after
    # it in the same session. Force a clean reload here, against the
    # real (unpatched) DEFAULT_LOG_PATH, so this test is not sensitive to
    # other test files' fixture ordering.
    import live_web.server as server_module
    importlib.reload(server_module)
    return server_module


def test_website_and_cli_share_the_same_default_log_path():
    server_module = _fresh_server_module()
    assert server_module.cli.log_path == live_auction_cli.DEFAULT_LOG_PATH == DEFAULT_LOG_PATH


def test_website_module_does_not_hardcode_a_separate_web_session_path():
    server_module = _fresh_server_module()
    assert "web_session" not in str(server_module.cli.log_path)
