"""Delivery outbox: a digest is committed as `pending` before sending and retried until sent.

out/outbox/<date>.json -> {"date", "subject", "status": "pending"|"sent", "attempts", ...}
The digest files themselves live in out/digests/<date>.{html,txt}.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from datetime import UTC, date, datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465
SENDER_NAME = "Bani în euro"
MAX_ATTEMPTS = 5  # then it stays in the archive and stops being retried


def enqueue(
    root: Path, day: date, subject: str, stem: str | None = None, attachments: list[str] = ()
) -> Path:
    """Queue digests/<stem>.{txt,html} (+ attachments from digests/) for sending."""
    stem = stem or day.isoformat()
    path = root / "outbox" / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "date": day.isoformat(),
        "stem": stem,
        "subject": subject,
        "attachments": list(attachments),
        "status": "pending",
        "attempts": 0,
        "last_error": None,
    }
    if path.exists():  # a forced re-run replaces the digest; send it again
        entry["attempts"] = json.loads(path.read_text()).get("attempts", 0)
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n")
    return path


def pending(root: Path) -> list[Path]:
    entries = []
    for path in sorted((root / "outbox").glob("*.json")):
        entry = json.loads(path.read_text())
        if entry["status"] == "pending" and entry["attempts"] < MAX_ATTEMPTS:
            entries.append(path)
    return entries


def build_message(root: Path, entry: dict, sender: str, to: str) -> EmailMessage:
    folder = root / "digests"
    message = EmailMessage()
    message["Subject"] = entry["subject"]
    message["From"] = formataddr((SENDER_NAME, sender))
    message["To"] = to
    message["Message-ID"] = make_msgid(domain="finance-digest")
    stem = entry.get("stem", entry["date"])
    message.set_content((folder / f"{stem}.txt").read_text(), charset="utf-8")
    message.add_alternative((folder / f"{stem}.html").read_text(), subtype="html", charset="utf-8")
    for name in entry.get("attachments", []):
        message.add_attachment((folder / name).read_bytes(), maintype="text", subtype="csv", filename=name)
    return message


def send_pending(root: Path, to: str | None = None) -> int:
    """Send every pending entry; returns 1 if any send failed (the entry stays pending)."""
    paths = pending(root)
    if not paths:
        print("outbox: nothing pending")
        return 0
    user = os.environ.get("MAIL_USERNAME", "").strip()
    # an app password pasted from a wrapped line carries spaces/newlines; Gmail shows it spaced anyway
    password = "".join(os.environ.get("MAIL_PASSWORD", "").split())
    if not (user and password):
        print("outbox: MAIL_USERNAME / MAIL_PASSWORD not set", file=sys.stderr)
        return 1
    try:
        smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=60)
        smtp.login(user, password)
    except (smtplib.SMTPException, OSError) as exc:  # entries stay pending for the next run
        print(f"outbox: SMTP connect/login failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    failed = 0
    with smtp:
        for path in paths:
            entry = json.loads(path.read_text())
            entry["attempts"] += 1
            try:
                smtp.send_message(build_message(root, entry, user, to or user))
            except (smtplib.SMTPException, OSError) as exc:
                entry["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
                failed += 1
                print(f"outbox: {path.stem} FAILED {entry['last_error']}", file=sys.stderr)
            else:
                entry |= {"status": "sent", "sent_at": datetime.now(UTC).isoformat(timespec="seconds")}
                print(f"outbox: {path.stem} sent")
            path.write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n")
    return 1 if failed else 0
