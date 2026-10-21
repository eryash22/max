"""Module 19 Enhancement — WhatsApp Web browser-automation session manager.

This backs `whatsapp_provider.BrowserAutomationProvider`. It owns a single,
persistent Playwright Chromium context (profile data kept on disk under
`settings.file_storage_path/whatsapp_session`, never returned to any API
client) so that once an admin scans the WhatsApp Web QR code, the login
survives backend restarts — exactly like a real "linked device" session on a
phone.

Playwright's sync API is not thread-safe across threads: every call must
happen on the thread that created the `Playwright` object. So the browser
lives on one dedicated background thread running `_worker_loop`; every public
method here submits a job to `_jobs` and blocks on a `Future` for the result.
FastAPI request threads never touch Playwright objects directly.

Honesty contract (see PROJECT_STATUS.md Module 19 Enhancement): this module
never reports SENT without DOM evidence that an outgoing message bubble
containing the exact rendered text appeared in the chat pane. WhatsApp Web's
DOM/selectors are not under our control and can change without notice; when
evidence can't be confirmed the result is `REQUIRES_USER_CONFIRMATION`, never
a guessed SENT. No delivery/read-receipt confirmation is attempted — only
send-completion evidence, which is the strongest signal browser automation
can honestly offer.
"""
from __future__ import annotations

import base64
import queue
import threading
import time
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from ..config import settings

STATE_DISCONNECTED = "DISCONNECTED"
STATE_CONNECTING = "CONNECTING"
STATE_AWAITING_QR_SCAN = "AWAITING_QR_SCAN"
STATE_CONNECTED = "CONNECTED"
STATE_ERROR = "ERROR"

SEND_QUEUED = "QUEUED"
SEND_OPENED = "OPENED"
SEND_MESSAGE_PREPARED = "MESSAGE_PREPARED"
SEND_REQUIRES_USER_CONFIRMATION = "REQUIRES_USER_CONFIRMATION"
SEND_SENT = "SENT"
SEND_FAILED = "FAILED"

_QR_WAIT_TIMEOUT_SECONDS = 120
_SEND_TIMEOUT_SECONDS = 45
_LOGIN_SELECTOR = "div[aria-label='Chat list'], div[data-testid='chat-list']"
_QR_SELECTOR = "canvas[aria-label], div[data-ref] canvas"
_COMPOSE_SELECTOR = "footer div[contenteditable='true']"
_OUTGOING_BUBBLE_SELECTOR = "div.message-out, div[data-testid='msg-container'][class*='message-out']"


@dataclass
class SendResult:
    state: str
    detail: str
    evidence: str | None = None


@dataclass
class _Status:
    state: str = STATE_DISCONNECTED
    qr_code_base64: str | None = None
    connected_at: str | None = None
    last_error: str | None = None
    last_checked_at: str | None = None


@dataclass
class _Job:
    kind: str
    payload: dict = field(default_factory=dict)
    result_queue: "queue.Queue" = field(default_factory=queue.Queue)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digits_only(recipient: str) -> str:
    return re.sub(r"[^0-9]", "", recipient)


class WhatsAppAutomationSession:
    """One process-wide instance — see module docstring for the threading model."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = _Status()
        self._jobs: "queue.Queue[_Job]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop_requested = False
        # Playwright objects — only ever touched on the worker thread.
        self._playwright = None
        self._context = None
        self._page = None

    # ---- public, thread-safe API -----------------------------------------

    def get_status(self) -> dict:
        with self._lock:
            s = self._status
            return {
                "state": s.state,
                "qr_code_base64": s.qr_code_base64,
                "connected_at": s.connected_at,
                "last_error": s.last_error,
                "last_checked_at": s.last_checked_at,
            }

    def connect(self) -> dict:
        self._ensure_worker_running()
        job = _Job(kind="CONNECT")
        self._jobs.put(job)
        return job.result_queue.get(timeout=10)

    def disconnect(self) -> dict:
        self._ensure_worker_running()
        job = _Job(kind="DISCONNECT")
        self._jobs.put(job)
        return job.result_queue.get(timeout=15)

    def is_connected(self) -> bool:
        return self.get_status()["state"] == STATE_CONNECTED

    def send_message(self, *, recipient: str, text: str) -> SendResult:
        if not self.is_connected():
            return SendResult(
                state=SEND_REQUIRES_USER_CONFIRMATION,
                detail="No active WhatsApp Web session (connect and scan the QR code first).",
            )
        digits = _digits_only(recipient)
        if not digits:
            return SendResult(state=SEND_FAILED, detail="Recipient has no usable phone digits.")
        self._ensure_worker_running()
        job = _Job(kind="SEND", payload={"digits": digits, "text": text})
        self._jobs.put(job)
        try:
            result: SendResult = job.result_queue.get(timeout=_SEND_TIMEOUT_SECONDS + 10)
        except queue.Empty:
            return SendResult(state=SEND_REQUIRES_USER_CONFIRMATION, detail="Automation timed out; verify manually.")
        return result

    # ---- worker thread ------------------------------------------------

    def _ensure_worker_running(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_requested = False
            self._thread = threading.Thread(target=self._worker_loop, name="whatsapp-automation", daemon=True)
            self._thread.start()

    def _set_status(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._status, key, value)
            self._status.last_checked_at = _now_iso()

    def _worker_loop(self) -> None:
        idle_since_connect_check = 0.0
        while True:
            try:
                job = self._jobs.get(timeout=1.0)
            except queue.Empty:
                if self.get_status()["state"] in (STATE_CONNECTING, STATE_AWAITING_QR_SCAN):
                    self._poll_login_state()
                continue

            if job.kind == "CONNECT":
                self._handle_connect(job)
            elif job.kind == "DISCONNECT":
                self._handle_disconnect(job)
            elif job.kind == "SEND":
                self._handle_send(job)

    def _handle_connect(self, job: _Job) -> None:
        try:
            if self._context is None:
                from playwright.sync_api import sync_playwright

                self._playwright = sync_playwright().start()
                session_dir = Path(settings.file_storage_path) / "whatsapp_session"
                session_dir.mkdir(parents=True, exist_ok=True)
                headless = settings.whatsapp_automation_headless
                self._context = self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(session_dir),
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
                self._page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
            self._set_status(state=STATE_CONNECTING, last_error=None)
            job.result_queue.put(self.get_status())
        except Exception as exc:  # noqa: BLE001 — surfaced to admin, never crashes the worker
            self._set_status(state=STATE_ERROR, last_error=str(exc))
            job.result_queue.put(self.get_status())

    def _poll_login_state(self) -> None:
        if self._page is None:
            return
        try:
            if self._page.locator(_LOGIN_SELECTOR).count() > 0:
                self._set_status(state=STATE_CONNECTED, qr_code_base64=None, connected_at=_now_iso(), last_error=None)
                return
            qr = self._page.locator(_QR_SELECTOR).first
            if qr.count() > 0:
                png_bytes = qr.screenshot()
                encoded = base64.b64encode(png_bytes).decode("ascii")
                self._set_status(state=STATE_AWAITING_QR_SCAN, qr_code_base64=encoded)
            else:
                self._set_status(state=STATE_CONNECTING)
        except Exception as exc:  # noqa: BLE001
            self._set_status(state=STATE_ERROR, last_error=str(exc))

    def _handle_disconnect(self, job: _Job) -> None:
        try:
            if self._context is not None:
                self._context.close()
            if self._playwright is not None:
                self._playwright.stop()
        except Exception as exc:  # noqa: BLE001
            self._set_status(last_error=str(exc))
        finally:
            self._context = None
            self._page = None
            self._playwright = None
            self._set_status(state=STATE_DISCONNECTED, qr_code_base64=None, connected_at=None)
            job.result_queue.put(self.get_status())

    def _handle_send(self, job: _Job) -> None:
        digits = job.payload["digits"]
        text = job.payload["text"]
        try:
            url = f"https://web.whatsapp.com/send?phone={digits}&text={quote(text)}"
            self._page.goto(url, wait_until="domcontentloaded")
            compose = self._page.locator(_COMPOSE_SELECTOR).last
            try:
                compose.wait_for(timeout=20000, state="visible")
            except Exception:
                job.result_queue.put(SendResult(
                    state=SEND_REQUIRES_USER_CONFIRMATION,
                    detail="Chat did not load (invalid number, or not a WhatsApp contact) — verify manually.",
                ))
                return

            compose.click()
            compose.press("Enter")

            try:
                self._page.wait_for_timeout(1500)
                bubbles = self._page.locator(_OUTGOING_BUBBLE_SELECTOR)
                found = False
                count = bubbles.count()
                for i in range(max(0, count - 5), count):
                    if text.strip()[:40] in (bubbles.nth(i).inner_text() or ""):
                        found = True
                        break
                if found:
                    job.result_queue.put(SendResult(
                        state=SEND_SENT,
                        detail="Outgoing message bubble with matching text detected in the chat pane.",
                        evidence="dom_outgoing_bubble_match",
                    ))
                else:
                    job.result_queue.put(SendResult(
                        state=SEND_REQUIRES_USER_CONFIRMATION,
                        detail="Message was typed and Enter was pressed, but no matching outgoing bubble was "
                               "confirmed in the DOM — verify the chat manually before assuming it sent.",
                    ))
            except Exception as exc:  # noqa: BLE001
                job.result_queue.put(SendResult(
                    state=SEND_REQUIRES_USER_CONFIRMATION,
                    detail=f"Could not verify send completion: {exc}",
                ))
        except Exception as exc:  # noqa: BLE001
            job.result_queue.put(SendResult(state=SEND_FAILED, detail=str(exc)))


_session: WhatsAppAutomationSession | None = None
_session_lock = threading.Lock()


def get_session() -> WhatsAppAutomationSession:
    global _session
    with _session_lock:
        if _session is None:
            _session = WhatsAppAutomationSession()
        return _session
