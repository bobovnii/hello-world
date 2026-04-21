"""Simple usage analytics for the MCP server."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from collections import defaultdict

logger = logging.getLogger(__name__)

ANALYTICS_FILE = Path("data/analytics.jsonl")


class UsageTracker:
    """Tracks tool calls, sessions, and usage patterns."""

    def __init__(self):
        self._counts: dict[str, int] = defaultdict(int)
        self._sessions: set[str] = set()
        self._daily: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        ANALYTICS_FILE.parent.mkdir(parents=True, exist_ok=True)

    def log_tool_call(self, tool_name: str, params: dict | None = None, session_id: str = ""):
        """Log a tool invocation."""
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")

        self._counts[tool_name] += 1
        self._daily[date_str][tool_name] += 1
        if session_id:
            self._sessions.add(session_id)

        # Append to JSONL file for persistence
        entry = {
            "timestamp": now.isoformat(),
            "tool": tool_name,
            "date": date_str,
            "session": session_id or "unknown",
        }
        if params:
            # Log key params (not full payloads)
            safe_params = {}
            for k in ("budget_max", "min_rooms", "property_type", "districts", "district", "listing_id"):
                if k in params:
                    safe_params[k] = params[k]
            if safe_params:
                entry["params"] = safe_params

        try:
            with open(ANALYTICS_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug(f"Analytics write failed: {e}")

    def get_stats(self) -> dict:
        """Get current usage statistics."""
        today = datetime.now().strftime("%Y-%m-%d")
        total_calls = sum(self._counts.values())

        # Read from file for accurate count
        sessions_all_time = set()
        daily_totals: dict[str, int] = defaultdict(int)
        tool_totals: dict[str, int] = defaultdict(int)

        try:
            if ANALYTICS_FILE.exists():
                with open(ANALYTICS_FILE) as f:
                    for line in f:
                        try:
                            entry = json.loads(line.strip())
                            sessions_all_time.add(entry.get("session", ""))
                            daily_totals[entry.get("date", "")] += 1
                            tool_totals[entry.get("tool", "")] += 1
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass

        return {
            "total_tool_calls": sum(tool_totals.values()),
            "unique_sessions": len(sessions_all_time - {"", "unknown"}),
            "calls_today": daily_totals.get(today, 0),
            "active_days": len(daily_totals),
            "tool_breakdown": dict(tool_totals),
            "top_tools": sorted(tool_totals.items(), key=lambda x: -x[1])[:5],
        }


# Singleton
tracker = UsageTracker()
