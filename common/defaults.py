"""One place for the relay address, so the viewer and the agent cannot drift apart."""

from __future__ import annotations

DEFAULT_RELAY_HOST = "desk.prasinospower.com"

# The viewer talks to the relay over HTTPS; the session websocket upgrades from there.
DEFAULT_VIEWER_URL = f"https://{DEFAULT_RELAY_HOST}"

# The agent keeps its own websocket connection open.
DEFAULT_AGENT_URL = f"wss://{DEFAULT_RELAY_HOST}/ws/agent"
