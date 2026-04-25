"""HTML report generation for the daily Telegram digest.

Each search produces one standalone HTML document attached after the
Markdown deal cards via Telegram's ``sendDocument``. See ``html.py``.
"""

from .html import build_digest_html

__all__ = ["build_digest_html"]
