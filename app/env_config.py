"""
Load .env files before any module reads configuration.

``app/.env`` (used by the original code) takes precedence over a project-root
``.env``. Real environment variables always win over both. Safe to import
multiple times, and a no-op if python-dotenv is not installed.
"""

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

_APP_DIR = Path(__file__).resolve().parent

if load_dotenv is not None:
    load_dotenv(_APP_DIR / ".env")
    load_dotenv(_APP_DIR.parent / ".env")
