"""Gmail alert when Food quit stops the bot after repeated eat failures.

Credentials come only from environment variables (loaded from a gitignored
``.env`` next to the project root). Missing password skips the send.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import certifi

SMTP_TIMEOUT_S = 15
_ENV_LOADED = False


def load_env_file(path: Path | None = None) -> None:
    """Load ``KEY=VALUE`` lines into ``os.environ`` without overriding existing keys.

    Args:
        path: Env file. Defaults to ``<repo>/.env``.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    env_path = path or Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def send_eat_quit_alert(
    *,
    kills: int,
    kills_per_hour: float,
    elapsed_minutes: float,
    eat_failures: int,
    started_local: str,
    stopped_local: str,
    attack_style: str,
    attack_method: str,
    last_action: str,
) -> bool:
    """Send one plain-text SAFE STOP mail. Returns True if SMTP accepted it.

    Raises:
        smtplib.SMTPException: Transport or auth failed after creds were present.
        OSError: Network failure.
        TimeoutError: SMTP timeout.
    """
    load_env_file()
    user = os.environ.get("RS3_SMTP_USER", "").strip()
    password = os.environ.get("RS3_SMTP_PASSWORD", "").replace(" ", "").strip()
    recipient = os.environ.get("RS3_ALERT_TO", "").strip()
    sender = os.environ.get("RS3_ALERT_FROM", "").strip() or user
    host = os.environ.get("RS3_SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.environ.get("RS3_SMTP_PORT", "587"))
    if not user or not password or not recipient:
        print("WARNING: eat-quit email skipped — set RS3_SMTP_USER, "
              "RS3_SMTP_PASSWORD, and RS3_ALERT_TO in .env")
        return False

    subject = f"RS3 SAFE STOP — eat failed x{eat_failures}"
    body = (
        f"Session kills: {kills}  ({kills_per_hour:.0f}/hr over {elapsed_minutes:.1f}m)\n"
        f"Started: {started_local}\n"
        f"Stopped: {stopped_local}\n"
        f"Duration: {elapsed_minutes:.1f} minutes\n"
        f"Eat failures: {eat_failures}\n"
        f"Attack: {attack_style} / {attack_method}\n"
        f"Food quit: on\n"
        f"Last action: {last_action}\n"
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)

    context = ssl.create_default_context(cafile=certifi.where())
    with smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT_S) as smtp:
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(message)
    print(f"Eat-quit email sent to {recipient}")
    return True
