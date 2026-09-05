"""Browser-session auth for strava.com internal endpoints.

Strava's internal JSON endpoints authenticate with the same cookies your browser
uses, plus an ``X-CSRF-TOKEN`` header. Rather than asking you to dig those out by
hand, we parse them straight out of a "Copy as cURL" command from devtools.

Flow:
    1. Open strava.com logged in, devtools -> Network.
    2. Load any page, right-click a request to strava.com -> Copy -> Copy as cURL.
    3. Paste it into a file and run: strava auth import <file>

The extracted credentials go to .secrets/session.json (gitignored).
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shlex
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests

SECRETS_DIR = Path(".secrets")
SESSION_FILE = SECRETS_DIR / "session.json"

# The cookie that actually carries the login. Everything else is optional.
REQUIRED_COOKIE = "_strava4_session"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:149.0) "
    "Gecko/20100101 Firefox/149.0"
)


class SessionError(RuntimeError):
    """Raised when credentials are missing, malformed, or rejected by Strava."""


@dataclass
class BrowserSession:
    """Credentials lifted from a logged-in browser session."""

    cookies: dict[str, str]
    csrf_token: str | None = None
    user_agent: str = DEFAULT_USER_AGENT
    imported_at: float = field(default_factory=time.time)

    # ---------------------------------------------------------------- parsing

    @classmethod
    def from_text(cls, text: str) -> BrowserSession:
        """Parse credentials out of text holding one or more cURL commands.

        Accepts a bare "Copy as cURL" paste or a whole scratch file with several
        captures and prose in between (see LOCAL/strava_web.md). The first
        command carrying a session cookie wins -- later captures in such a file
        tend to be from the same browser session anyway.
        """
        commands = extract_curl_commands(text) or [text]
        first_error: SessionError | None = None
        for command in commands:
            try:
                return cls.from_curl(command)
            except SessionError as exc:
                first_error = first_error or exc
        raise first_error or SessionError("No cURL command found in that input.")

    @classmethod
    def from_curl(cls, command: str) -> BrowserSession:
        """Parse a single devtools "Copy as cURL" command.

        Handles both the Firefox style (``-H 'Cookie: ...'``) and the Chrome
        style (``-b 'a=1; b=2'``), and tolerates the backslash-newline
        continuations both browsers emit.
        """
        # shlex renders an escaped newline as a whitespace token; drop those.
        tokens = [t for t in shlex.split(command) if t.strip()]

        headers: dict[str, str] = {}
        cookie_header = ""
        expect: str | None = None

        for token in tokens:
            if expect == "header":
                name, sep, value = token.partition(":")
                if sep:
                    headers[name.strip().lower()] = value.strip()
                expect = None
            elif expect == "cookie":
                cookie_header = f"{cookie_header}; {token}" if cookie_header else token
                expect = None
            elif token in ("-H", "--header"):
                expect = "header"
            elif token in ("-b", "--cookie"):
                expect = "cookie"

        if "cookie" in headers:
            joined = headers["cookie"]
            cookie_header = f"{cookie_header}; {joined}" if cookie_header else joined

        cookies = _parse_cookie_header(cookie_header)
        if REQUIRED_COOKIE not in cookies:
            raise SessionError(
                f"No {REQUIRED_COOKIE} cookie in that cURL command. Make sure you "
                "copied a request to strava.com while logged in (a request to a "
                "CDN or analytics host won't carry the session cookie)."
            )

        return cls(
            cookies=cookies,
            csrf_token=headers.get("x-csrf-token"),
            user_agent=headers.get("user-agent", DEFAULT_USER_AGENT),
        )

    # ------------------------------------------------------------- persistence

    @classmethod
    def load(cls, path: Path = SESSION_FILE) -> BrowserSession:
        if not path.exists():
            raise SessionError(
                f"No saved session at {path}. Copy a strava.com request from "
                "devtools as cURL, then run: strava auth import <file>"
            )
        raw = json.loads(path.read_text())
        return cls(
            cookies=raw["cookies"],
            csrf_token=raw.get("csrf_token"),
            user_agent=raw.get("user_agent", DEFAULT_USER_AGENT),
            imported_at=raw.get("imported_at", 0.0),
        )

    def save(self, path: Path = SESSION_FILE) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "cookies": self.cookies,
                    "csrf_token": self.csrf_token,
                    "user_agent": self.user_agent,
                    "imported_at": self.imported_at,
                },
                indent=2,
            )
        )
        path.chmod(0o600)
        return path

    # ------------------------------------------------------------------ expiry

    def expires_at(self) -> datetime | None:
        """Best-effort session expiry, read from the ``_strava_idcf`` JWT.

        Strava doesn't expose an expiry for ``_strava4_session`` itself, so this
        is a hint rather than a guarantee: the session often outlives it, and can
        also be revoked earlier by logging out elsewhere.
        """
        token = self.cookies.get("_strava_idcf")
        if not token:
            return None
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
        except (IndexError, KeyError, ValueError, binascii.Error):
            return None

    def describe(self) -> str:
        lines = [
            f"cookies:    {len(self.cookies)} "
            f"({', '.join(sorted(self.cookies)[:4])}...)",
            f"csrf token: {'present' if self.csrf_token else 'MISSING'}",
            f"imported:   {_ago(self.imported_at)}",
        ]
        expiry = self.expires_at()
        if expiry:
            lines.append(f"hint expiry: {expiry:%Y-%m-%d %H:%M UTC} ({_ago(expiry.timestamp())})")
        return "\n".join(lines)

    # ---------------------------------------------------------------- requests

    def build(self) -> requests.Session:
        """A requests.Session pre-loaded with the browser's identity."""
        s = requests.Session()
        for name, value in self.cookies.items():
            s.cookies.set(name, value, domain=".strava.com")
        s.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.strava.com/",
            }
        )
        if self.csrf_token:
            s.headers["X-CSRF-TOKEN"] = self.csrf_token
        return s


def extract_curl_commands(text: str) -> list[str]:
    """Pull every ``curl ...`` command out of a block of text.

    A command runs from a line starting with ``curl`` until the first line that
    does not end in a backslash continuation, so surrounding markdown headings
    and prose are skipped rather than fed to shlex (where stray apostrophes
    would blow up the tokenizer).
    """
    commands: list[str] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("curl "):
            buffer: list[str] = []
            while i < len(lines):
                line = lines[i].rstrip()
                buffer.append(line)
                i += 1
                if not line.endswith("\\"):
                    break
            commands.append("\n".join(buffer))
        else:
            i += 1
    return commands


def _parse_cookie_header(header: str) -> dict[str, str]:
    """Split a Cookie header into a dict.

    Strava's cookie values contain ``=``, ``%``, and JSON-ish braces, so we split
    only on the first ``=`` of each ``;``-separated pair. Values are kept exactly
    as the browser sent them -- no unquoting, or CloudFront signatures break.
    """
    cookies: dict[str, str] = {}
    for pair in header.split(";"):
        name, sep, value = pair.strip().partition("=")
        if sep and name:
            cookies[name.strip()] = value.strip()
    return cookies


def _ago(ts: float) -> str:
    if not ts:
        return "unknown"
    delta = time.time() - ts
    tense = "ago" if delta >= 0 else "from now"
    seconds = abs(delta)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds / size:.0f}{unit} {tense}"
    return f"{seconds:.0f}s {tense}"


def redact(text: str) -> str:
    """Strip cookie/token values out of text before it reaches a log or terminal."""
    text = re.sub(r"(_strava4_session=)[^;\s\"']+", r"\1<redacted>", text)
    text = re.sub(r"(X-CSRF-TOKEN:\s*)\S+", r"\1<redacted>", text, flags=re.I)
    return text
