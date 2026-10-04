"""
Secret redaction for anything that can leave the process: API error
messages, log records (including tracebacks), console output and text that is
sent to an LLM.

Three layers are applied to every string:

1. VALUES    the actual value of every sensitive environment variable
             (names containing PASSWORD / PASSWD / SECRET / TOKEN / API_KEY /
             PRIVATE_KEY / SALT), in plain and URL-encoded form.
2. URLS      credentials embedded in a URL (user and password before the @)
3. KEY=VALUE any password / api_key / token style ``name=value`` pair

``install_log_redaction()`` hooks the logging record factory so *every*
logger, in every handler, is covered, including exception tracebacks.

Standard library only.
"""

from __future__ import annotations

import logging
import os
import re
import traceback
from urllib.parse import quote, quote_plus

REDACTED = "***"

# Environment variables whose VALUES are treated as secrets.
_SENSITIVE_ENV_NAME = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|PRIVATE_?KEY|SALT)", re.IGNORECASE
)

# Shorter values would redact ordinary words ("pw", "db") and mangle messages.
_MIN_SECRET_LENGTH = 4

_URL_CREDENTIALS = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<user>[^/\s:@]*):(?P<password>[^@\s/]+)@"
)

_KEY_VALUE = re.compile(
    r"""(?ix)
    \b(?P<key>password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key)\b
    (?P<sep>\s*[=:]\s*)
    (?P<value>'[^']*'|"[^"]*"|[^\s,;&)'"]+)
    """
)


def secret_values() -> list[str]:
    """Current secret values from the environment, longest first."""

    values: set[str] = set()

    for name, value in os.environ.items():
        if value and len(value) >= _MIN_SECRET_LENGTH and _SENSITIVE_ENV_NAME.search(name):
            values.add(value)
            values.add(quote(value, safe=""))
            values.add(quote_plus(value))

    return sorted(values, key=len, reverse=True)


def redact(text: object) -> str:
    """Return ``text`` (any object) as a string with secrets masked."""

    result = str(text)

    if not result:
        return result

    for secret in secret_values():
        if secret in result:
            result = result.replace(secret, REDACTED)

    result = _URL_CREDENTIALS.sub(
        lambda m: f"{m.group('scheme')}{m.group('user')}:{REDACTED}@", result
    )

    result = _KEY_VALUE.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}{REDACTED}", result
    )

    return result


def contains_secret(text: object) -> bool:
    """True if ``text`` still contains a configured secret value."""

    value = str(text)

    return any(secret in value for secret in secret_values())


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

_installed_factory = None


def install_log_redaction() -> None:
    """
    Redact secrets in every log record, process-wide. Idempotent.

    Done in the record factory (not a handler filter) so it also covers
    handlers added later by uvicorn or other libraries. Exception tracebacks
    are rendered here, redacted, and stored as ``exc_text``.
    """

    global _installed_factory

    if _installed_factory is not None:
        return

    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)

        try:
            message = record.getMessage()
            redacted = redact(message)

            if redacted != message:
                record.msg = redacted
                record.args = ()

            if record.exc_info and record.exc_info[0] is not None:
                rendered = "".join(traceback.format_exception(*record.exc_info))
                record.exc_text = redact(rendered)
                record.exc_info = None

            if record.stack_info:
                record.stack_info = redact(record.stack_info)

        except Exception:  # noqa: BLE001  logging must never raise
            pass

        return record

    logging.setLogRecordFactory(factory)
    _installed_factory = (previous, factory)


def uninstall_log_redaction() -> None:
    """Restore the previous record factory (used by tests)."""

    global _installed_factory

    if _installed_factory is None:
        return

    previous, factory = _installed_factory

    if logging.getLogRecordFactory() is factory:
        logging.setLogRecordFactory(previous)

    _installed_factory = None
