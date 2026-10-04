"""
Scan the repository for hard-coded credentials.

    python -m app.hardening_tools scan-secrets

What it looks for (matched VALUES are never printed):

  credential-literal   NAME = "value" where NAME contains password / secret /
                       token / api_key / private_key ...
  getenv-default       os.getenv("X_PASSWORD", "value")  (a secret default)
  url-credentials      scheme://user:password@host
  root-account         hard-coded administrative account in code or URLs
  api-key              well-known key shapes (Google, OpenAI, Groq, LangSmith,
                       GitHub, AWS, Slack) and PEM private keys
  tracked-secret-file  .env / *.pem / *.key / uploaded databases that git tracks

Intentional exceptions:
  * a line ending in ``# noqa: secret`` (use sparingly, explain why)
  * clearly fake values: empty, ``***``, ``<...>``, ``${...}``, CHANGE_ME,
    ``FAKE-...`` / ``fake-...`` and anything containing "not-real"

IMPORTANT: this scans the WORKING TREE only. A secret that was committed and
later deleted is still in git history; this tool cannot see it. See
docs/SECURITY.md for how to check and clean history.

Standard library only.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
}
SKIP_PATH_PREFIXES = ("data/chroma", "data/spiderman", "data/user_dbs")
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pyc", ".sqlite",
    ".sqlite3", ".db", ".zip", ".woff", ".woff2", ".ttf",
}
MAX_FILE_BYTES = 8 * 1024 * 1024

# This file contains the rule definitions themselves.
SELF = Path(__file__).resolve()

_SECRET_NAME = (
    r"[A-Za-z0-9_.\-]*"
    r"(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|apikey|private[_-]?key)"
    r"[A-Za-z0-9_.\-]*"
)

_CREDENTIAL_LITERAL = re.compile(
    rf"""(?ix)
    (?P<name>{_SECRET_NAME})
    ["']?\]?\s*(?:=|:|=>)\s*
    (?P<quote>["'])(?P<value>[^"'\n]+)(?P=quote)
    """
)

_GETENV_DEFAULT = re.compile(
    r"""(?ix)
    getenv\(\s*["'][^"']*(?:password|passwd|secret|token|api_?key)[^"']*["']\s*,\s*
    (?P<quote>["'])(?P<value>[^"'\n]+)(?P=quote)
    """
)

_URL_CREDENTIALS = re.compile(
    r"""(?ix)
    (?P<scheme>[a-z][a-z0-9+.\-]*://)
    (?P<user>[^/\s:@'"{}$<>]+):
    (?P<value>[^/\s@'"{}$<>]+)@
    """
)

_ROOT_PATTERNS = [
    re.compile(r"""(?i)\b(?:user|username|MYSQL_USER)\b["']?\s*[:=]\s*["']root["']"""),
    re.compile(r"""(?i)://root[:@]"""),
    re.compile(r"""(?i)getenv\(\s*["']MYSQL_USER["']\s*,\s*["']root["']"""),
]

_API_KEY_SHAPES = re.compile(
    r"AIza[0-9A-Za-z_\-]{20,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|gsk_[A-Za-z0-9]{20,}"
    r"|lsv2_[A-Za-z0-9_]{10,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"
)

_PLACEHOLDER = re.compile(
    r"""(?ix)^(
        \*+ | x{3,} | <.*> | \$\{.*\} | \{.*\} | %\(.*\)s
      | change[-_ ]?me | changeme | your[-_ ].* | example.* | dummy.* | placeholder.*
      | fake[-_ ].* | .*not[-_ ]real.*
      | (?-i:[A-Z][A-Z0-9_]{3,})            # an UPPERCASE environment variable NAME (case-sensitive!)
    )$"""
)

_ALLOW_MARKER = re.compile(r"noqa:\s*secret|pragma:\s*allowlist\s+secret", re.IGNORECASE)

_SECRET_FILE_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
    "credentials*.json", "service-account*.json", "grants*.sql",
    "*.sqlite", "*.sqlite3", "registry.db",
]
_SECRET_FILE_ALLOWED = {".env.example", ".env.sample", ".env.template"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    excerpt: str          # already masked

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: [{self.rule}] {self.excerpt}"


def _is_placeholder(value: str) -> bool:
    return not value.strip() or bool(_PLACEHOLDER.match(value.strip()))


def _skip(path: Path, relative: str) -> bool:
    if path.resolve() == SELF:
        return True

    if any(part in SKIP_DIRS for part in path.parts):
        return True

    if relative.startswith(SKIP_PATH_PREFIXES):
        return True

    if path.name in SKIP_FILES or path.suffix.lower() in SKIP_SUFFIXES:
        return True

    try:
        return path.stat().st_size > MAX_FILE_BYTES
    except OSError:
        return True


def scan_text(relative: str, text: str) -> list[Finding]:
    """Scan one file's text. ``relative`` is its path relative to the root."""

    findings: list[Finding] = []
    in_tests = relative.startswith("tests/")

    for number, line in enumerate(text.splitlines(), start=1):
        if _ALLOW_MARKER.search(line):
            continue

        def add(rule: str, excerpt: str) -> None:
            findings.append(Finding(relative, number, rule, excerpt))

        for match in _CREDENTIAL_LITERAL.finditer(line):
            if not _is_placeholder(match.group("value")):
                add("credential-literal", f"{match.group('name')} = \"****\"")

        match = _GETENV_DEFAULT.search(line)
        if match and not _is_placeholder(match.group("value")):
            add("getenv-default", "getenv(<secret name>, \"****\")")

        for match in _URL_CREDENTIALS.finditer(line):
            if not _is_placeholder(match.group("value")):
                add(
                    "url-credentials",
                    f"{match.group('scheme')}{match.group('user')}:****@",
                )

        if not in_tests:
            for pattern in _ROOT_PATTERNS:
                if pattern.search(line):
                    add("root-account", "hard-coded administrative account")
                    break

        match = _API_KEY_SHAPES.search(line)
        if match:
            add("api-key", match.group(0)[:4] + "…")

    return findings


def tracked_secret_files(root: Path) -> list[Finding]:
    """Secret-like files that git TRACKS (needs a .git directory and git)."""

    if not (root / ".git").exists():
        return []

    try:
        output = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    findings = []

    for tracked in output.splitlines():
        name = Path(tracked).name

        if name in _SECRET_FILE_ALLOWED:
            continue

        if any(fnmatch.fnmatch(name, pattern) for pattern in _SECRET_FILE_PATTERNS):
            findings.append(Finding(tracked, 0, "tracked-secret-file", "tracked by git"))

    return findings


def scan_repository(root: str | Path | None = None) -> list[Finding]:
    """Scan every text file under ``root`` (default: the project)."""

    base = Path(root or PROJECT_ROOT).resolve()
    findings: list[Finding] = []

    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue

        relative = path.relative_to(base).as_posix()

        if _skip(path, relative):
            continue

        try:
            raw = path.read_bytes()
        except OSError:
            continue

        if b"\x00" in raw[:4096]:
            continue            # binary

        findings.extend(scan_text(relative, raw.decode("utf-8", errors="replace")))

    findings.extend(tracked_secret_files(base))

    return findings
