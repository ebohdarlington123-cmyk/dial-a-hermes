"""Dial-a-Hermes: a Hermes gateway platform for USSD (+ SMS) on basic feature phones.

Inbound: an aiohttp server answers Africa's Talking USSD callbacks at ``POST /ussd/<token>``.
Outbound: Hermes replies land in a per-phone buffer the USSD menu pages through; optionally the
full reply also goes out by SMS through the Africa's Talking SMS API. Dangerous-command
approvals and clarify questions use Hermes's own gateway approval / clarify flow, answered on
the keypad.

Network calls (disclosed in README.md): only to Africa's Talking's SMS API
(``api.sandbox.africastalking.com`` or ``api.africastalking.com``) and only when
``USSD_SMS_MODE=africastalking``. The callback listener binds ``USSD_HOST:USSD_PORT``
(default 127.0.0.1:8095); expose it with your own tunnel or reverse proxy.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, SendResult
from gateway.platforms.event import MessageEvent, MessageType, ProcessingOutcome
from gateway.platforms._shared import extra_or_secret, get_scoped_secret, seed_extra_from_env

try:
    import aiohttp
    from aiohttp import web
    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover - declared dependency
    aiohttp = None  # type: ignore[assignment]
    web = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False

from . import ussd_core as core

logger = logging.getLogger(__name__)

PLATFORM_NAME = "ussd"
PLUGIN_NAME = "dial-a-hermes"
DEFAULT_HOST, DEFAULT_PORT = "127.0.0.1", 8095
MAX_BODY_BYTES = 16_384  # AT callbacks are a few hundred bytes
MIN_TOKEN_LEN = 16
AT_SMS_URLS = {"sandbox": "https://api.sandbox.africastalking.com/version1/messaging",
               "live": "https://api.africastalking.com/version1/messaging"}
SMS_MODES = ("off", "africastalking", "simulator")
SIMULATOR_DIR = Path(__file__).parent / "simulator"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

PLATFORM_HINT = (
    "You are talking to the user over USSD on a basic feature phone. Your reply is shown on a tiny "
    "screen of about 170 characters, page by page, so write plain text only: no markdown, tables, "
    "code blocks or emoji. Keep answers short (under 500 characters) unless the user asks for more. "
    "Reply in the language the user writes in, including Nigerian Pidgin."
)


def _setting(extra: Optional[dict], key: str, env: str, default: Any = "") -> Any:
    return extra_or_secret(extra, key, env, default)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        return min(hi, max(lo, float(value)))
    except (TypeError, ValueError):
        return default


class UssdAdapter(BasePlatformAdapter):
    """USSD <-> Hermes. One Hermes chat per allowlisted phone number."""

    # Approval cards and replies are paged on the phone, never cut: let the gateway hand us the
    # whole text in one send() so the exec-approval card always carries the full command.
    MAX_MESSAGE_LENGTH = 16_000

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform(PLATFORM_NAME))
        extra = getattr(config, "extra", None) or {}
        self._host = str(_setting(extra, "host", "USSD_HOST", DEFAULT_HOST)).strip() or DEFAULT_HOST
        try:
            self._port = int(_setting(extra, "port", "USSD_PORT", DEFAULT_PORT))
        except (TypeError, ValueError):
            self._port = DEFAULT_PORT
        self._token = str(_setting(extra, "callback_token", "USSD_CALLBACK_TOKEN", "")).strip()
        cc = str(_setting(extra, "default_country_code", "USSD_DEFAULT_COUNTRY_CODE", "234"))
        self._allow = core.Allowlist.parse(str(_setting(extra, "allowed_users", "USSD_ALLOWED_USERS", "")), cc)
        lang = str(_setting(extra, "language", "USSD_LANGUAGE", "en")).strip().lower()
        self._sms_mode = str(_setting(extra, "sms_mode", "USSD_SMS_MODE", "off")).strip().lower()
        if self._sms_mode not in SMS_MODES:
            logger.warning("[ussd] unknown USSD_SMS_MODE %r, SMS disabled", self._sms_mode)
            self._sms_mode = "off"
        self._sms_policy = str(_setting(extra, "sms_replies", "USSD_SMS_REPLIES", "long")).strip().lower()
        self._simulator = _truthy(_setting(extra, "simulator", "USSD_SIMULATOR", "false"))
        self._at_username = str(_setting(extra, "at_username", "AT_USERNAME", "sandbox")).strip() or "sandbox"
        self._at_sender = str(_setting(extra, "at_sender_id", "AT_SENDER_ID", "")).strip()
        self._store = core.StateStore(default_lang=lang)
        self.sms_enabled = self._sms_mode != "off"
        self._engine = core.UssdEngine(
            self._store, self,
            screen_chars=int(_float(_setting(extra, "screen_chars", "USSD_SCREEN_CHARS", 182), 182, 60, 182)),
            reply_wait=_float(_setting(extra, "reply_wait_seconds", "USSD_REPLY_WAIT_SECONDS", 6), 6, 0, 9),
        )
        self._runner = None
        self._http: Optional["aiohttp.ClientSession"] = None
        self._sim_outbox: dict[str, list[dict]] = {}
        self._prefs_loaded = False

    # ── lifecycle ────────────────────────────────────────────────────────────────────────
    def _config_error(self) -> Optional[str]:
        if not AIOHTTP_AVAILABLE:
            return "aiohttp is not installed"
        if not self._allow:
            return ("USSD_ALLOWED_USERS is empty: refusing to start. Set it to the phone number(s) "
                    "allowed to control this Hermes, e.g. +2348031234567.")
        if len(self._token) < MIN_TOKEN_LEN:
            return (f"USSD_CALLBACK_TOKEN must be at least {MIN_TOKEN_LEN} random characters: it is the "
                    "secret path Africa's Talking posts to (/ussd/<token>).")
        if self._simulator and self._host not in _LOOPBACK:
            return "USSD_SIMULATOR=true is only allowed when USSD_HOST is a loopback address."
        if self._sms_mode == "africastalking" and not get_scoped_secret("AT_API_KEY", "").strip():
            return "USSD_SMS_MODE=africastalking needs AT_API_KEY (and AT_USERNAME, 'sandbox' for the sandbox)."
        return None

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        err = self._config_error()
        if err:
            logger.error("[ussd] %s", err)
            self._set_fatal_error("ussd_config", f"[ussd] {err}", retryable=False)
            return False
        self._load_prefs()
        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.router.add_post("/ussd/{token}", self._handle_callback)
        app.router.add_get("/health", lambda _r: web.Response(text="ok"))
        if self._simulator:
            app.router.add_get("/simulator/{token}", self._handle_simulator_page)
            app.router.add_get("/simulator/{token}/sms", self._handle_simulator_sms)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        try:
            await site.start()
        except OSError as exc:
            await self._runner.cleanup()
            self._runner = None
            self._set_fatal_error("ussd_bind", f"[ussd] cannot listen on {self._host}:{self._port}: {exc}",
                                  retryable=True)
            return False
        if self.sms_enabled and self._sms_mode == "africastalking":
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self._running = True
        self._mark_connected()
        logger.info("[ussd] listening on http://%s:%d/ussd/<token> (%d allowed number(s), sms=%s)",
                    self._host, self._port, len(self._allow.entries), self._sms_mode)
        if self._simulator:
            logger.info("[ussd] simulator: http://%s:%d/simulator/<USSD_CALLBACK_TOKEN>", self._host, self._port)
        return True

    async def disconnect(self) -> None:
        if self._http:
            await self._http.close()
            self._http = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._running = False
        self._mark_disconnected()

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": core.redact_phone(chat_id), "type": "dm"}

    def format_message(self, content: str) -> str:
        return core.plain_text(content)

    # ── inbound USSD ─────────────────────────────────────────────────────────────────────
    def _token_ok(self, request) -> bool:
        given = request.match_info.get("token", "")
        return bool(self._token) and hmac.compare_digest(given.encode(), self._token.encode())

    async def _handle_callback(self, request) -> "web.Response":
        if not self._token_ok(request):
            return web.Response(status=404, text="not found")
        try:
            if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
                return web.Response(status=413, text="too large")
            raw = await request.read()
            if len(raw) > MAX_BODY_BYTES:
                return web.Response(status=413, text="too large")
            if (request.content_type or "").endswith("json"):
                form = json.loads(raw.decode("utf-8") or "{}")
            else:
                form = urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True)
            req = core.parse_at_request(form)
        except (core.BadRequest, ValueError, UnicodeDecodeError) as exc:
            logger.warning("[ussd] bad callback: %s", exc)
            return web.Response(status=400, text="bad request")
        body = await self.handle_ussd(req)
        return web.Response(text=body, content_type="text/plain")

    async def handle_ussd(self, req: core.UssdRequest) -> str:
        """One USSD step: allowlist, then the menu engine. Always answers within the window."""
        user_key = self._allow.match(req.phone)
        if user_key is None:
            logger.warning("[ussd] refused number %s", core.redact_phone(req.phone))
            return core.format_response(core.END, core.tr(self._store.default_lang, "not_allowed"))
        try:
            return await self._engine.handle(req, user_key)
        except Exception:  # never leave a caller hanging on a stack trace
            logger.exception("[ussd] engine error")
            lang = self._store.phone(user_key).lang
            return core.format_response(core.END, core.tr(lang, "error"))

    # ── Backend protocol used by the engine ──────────────────────────────────────────────
    async def submit(self, state: core.PhoneState, text: str) -> None:
        state.turn_active, state.turn_started = True, time.time()
        state.reply_parts, state.last_reply, state.last_failed = [], "", False
        state.sms_sent_for_last = False
        state.touch()
        mid = uuid.uuid4().hex
        source = self.build_source(chat_id=state.user_key, chat_name=f"USSD {core.redact_phone(state.user_key)}",
                                   chat_type="dm", user_id=state.user_key, user_name=state.user_key,
                                   message_id=mid)
        event = MessageEvent(text=text, message_type=MessageType.TEXT, source=source, message_id=mid)
        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def resolve_approval(self, state: core.PhoneState, approval: core.PendingApproval, choice: str) -> bool:
        if choice != "deny" and choice not in approval.choices:
            return False
        from tools.approval import resolve_gateway_approval_card
        try:
            n = resolve_gateway_approval_card(approval.session_key, choice, approval.request_id,
                                              actor_id=state.user_key, extra=self.config.extra)
        except Exception:
            logger.exception("[ussd] approval resolve failed")
            return False
        logger.info("[ussd] approval %s -> %s by %s (resolved=%d)", approval.request_id, choice,
                    core.redact_phone(state.user_key), n)
        return n > 0

    async def resolve_clarify(self, state: core.PhoneState, clarify: core.PendingClarify, response: str) -> bool:
        from tools.clarify_gateway import resolve_gateway_clarify
        try:
            return bool(resolve_gateway_clarify(clarify.clarify_id, response))
        except Exception:
            logger.exception("[ussd] clarify resolve failed")
            return False

    def save_lang(self, state: core.PhoneState) -> None:
        prefs = {k: v.lang for k, v in self._store.phones.items()}
        try:
            path = self._prefs_path()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(prefs), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            logger.debug("[ussd] could not save language prefs", exc_info=True)

    # ── outbound (gateway -> phone) ──────────────────────────────────────────────────────
    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[dict[str, Any]] = None) -> SendResult:
        user_key = self._allow.match(chat_id)
        if user_key is None:
            return SendResult(success=False, error="number is not in USSD_ALLOWED_USERS")
        state = self._store.phone(user_key)
        text = core.plain_text(content)
        if not text:
            return SendResult(success=True, message_id=uuid.uuid4().hex)
        if state.turn_active:
            state.reply_parts.append(text)
        else:  # out-of-turn delivery (cron, notices): becomes the latest reply
            state.last_reply, state.sms_sent_for_last = text, False
            await self._maybe_sms(state)
        state.touch()
        return SendResult(success=True, message_id=uuid.uuid4().hex)

    def _parked_for_replay(self, event: MessageEvent) -> bool:
        """True if the gateway queued this event (startup restore) instead of running it yet.

        While the gateway restores sessions at boot it parks inbound messages and replays them
        later; the first pass still fires on_processing_complete with nothing sent. Treating that
        as the end of the turn would show "No reply" on the phone, so we keep the turn open.
        """
        try:
            runner = getattr(self._message_handler, "__self__", None)
            queue = getattr(runner, "_startup_restore_queue", None) or []
            return any(e is event for e in queue)
        except Exception:
            return False

    async def on_processing_complete(self, event: MessageEvent, outcome: ProcessingOutcome) -> None:
        user_key = self._allow.match(getattr(event.source, "chat_id", "") or "")
        if user_key is None:
            return
        state = self._store.phone(user_key)
        if not state.turn_active:
            return
        if outcome == ProcessingOutcome.SUCCESS and not state.reply_parts and self._parked_for_replay(event):
            return  # the gateway parked this message during startup restore; it will be replayed
        state.turn_active = False
        failed = outcome == ProcessingOutcome.FAILURE
        state.last_failed = failed
        reply = "\n\n".join(p for p in state.reply_parts if p)
        if not reply:
            reply = core.tr(state.lang, "turn_failed" if failed or outcome == ProcessingOutcome.CANCELLED
                            else "no_reply")
        state.last_reply, state.reply_parts = reply, []
        state.approvals.clear()  # a finished turn has nobody waiting on its approvals
        state.clarify = None
        state.touch()
        await self._maybe_sms(state)

    async def _send_exec_approval_prompt(self, prompt) -> SendResult:
        """Render Hermes's exec-approval request on the keypad (1 Approve / 2 Deny)."""
        user_key = self._allow.match(prompt.chat_id)
        if user_key is None:
            return SendResult(success=False, error="number is not in USSD_ALLOWED_USERS")
        if not prompt.request_id:
            return SendResult(success=False, error="approval without request id")
        state = self._store.phone(user_key)
        state.approvals = [a for a in state.approvals if a.request_id != prompt.request_id]
        state.approvals.append(core.PendingApproval(
            request_id=prompt.request_id, session_key=prompt.session_key, command=str(prompt.command or ""),
            description=str(prompt.description or ""), choices=[c for c in prompt.choices if c in ("once", "deny")]))
        state.touch()
        return SendResult(success=True, message_id=prompt.request_id)

    async def send_clarify(self, chat_id: str, question: str, choices: Optional[list], clarify_id: str,
                           session_key: str, metadata: Optional[dict[str, Any]] = None) -> SendResult:
        user_key = self._allow.match(chat_id)
        if user_key is None:
            return SendResult(success=False, error="number is not in USSD_ALLOWED_USERS")
        state = self._store.phone(user_key)
        state.clarify = core.PendingClarify(clarify_id=clarify_id, question=core.plain_text(question),
                                            choices=[core.plain_text(str(c)) for c in (choices or [])])
        state.touch()
        return SendResult(success=True, message_id=clarify_id)

    # ── SMS ──────────────────────────────────────────────────────────────────────────────
    async def _maybe_sms(self, state: core.PhoneState) -> None:
        if not self.sms_enabled or not state.last_reply or state.sms_sent_for_last:
            return
        if self._sms_policy == "off":
            return
        if self._sms_policy == "long":
            if len(self._engine._reply_pages(state)) <= 1:
                return
        ok = await self.send_sms(state, state.last_reply)
        state.sms_sent_for_last = ok

    async def send_sms(self, state: core.PhoneState, text: str) -> bool:
        text = core.plain_text(text)[:1600]
        if self._sms_mode == "simulator":
            box = self._sim_outbox.setdefault(state.user_key, [])
            box.append({"id": uuid.uuid4().hex, "at": time.time(), "text": text})
            del box[:-20]
            return True
        if self._sms_mode != "africastalking":
            return False
        api_key = get_scoped_secret("AT_API_KEY", "").strip()
        if not api_key:
            return False
        url = AT_SMS_URLS["sandbox" if self._at_username == "sandbox" else "live"]
        form = {"username": self._at_username, "to": "+" + core._digits(state.user_key), "message": text}
        if self._at_sender:
            form["from"] = self._at_sender
        session = self._http or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        try:
            async with session.post(url, data=form, headers={"apiKey": api_key, "Accept": "application/json"}) as r:
                body = await r.text()
                if r.status >= 400:
                    logger.warning("[ussd] AT SMS failed: HTTP %s %s", r.status, body[:200])
                    return False
                try:
                    recips = json.loads(body).get("SMSMessageData", {}).get("Recipients", [])
                    ok = any(str(x.get("status", "")).lower() == "success" for x in recips)
                except Exception:
                    ok = False
                if not ok:
                    logger.warning("[ussd] AT SMS not accepted: %s", body[:200])
                return ok
        except Exception as exc:
            logger.warning("[ussd] AT SMS error: %s", exc)
            return False
        finally:
            if session is not self._http:
                await session.close()

    # ── simulator (loopback only, token-gated) ───────────────────────────────────────────
    async def _handle_simulator_page(self, request) -> "web.Response":
        if not self._token_ok(request):
            return web.Response(status=404, text="not found")
        html = (SIMULATOR_DIR / "index.html").read_text(encoding="utf-8")
        cfg = {"callback": f"/ussd/{self._token}", "sms": f"/simulator/{self._token}/sms",
               "phone": self._allow.entries[0] if self._allow.entries else "", "serviceCode": "*384*4242#"}
        html = html.replace("/*__DAH_CONFIG__*/", "window.DAH_CONFIG = " + json.dumps(cfg) + ";")
        return web.Response(text=html, content_type="text/html",
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    async def _handle_simulator_sms(self, request) -> "web.Response":
        if not self._token_ok(request):
            return web.Response(status=404, text="not found")
        user_key = self._allow.match(request.query.get("phone", ""))
        items = self._sim_outbox.get(user_key, []) if user_key else []
        return web.json_response({"messages": items})

    # ── prefs ────────────────────────────────────────────────────────────────────────────
    def _prefs_path(self) -> Path:
        from plugins.plugin_storage import plugin_data_dir
        return plugin_data_dir(PLUGIN_NAME) / "language.json"

    def _load_prefs(self) -> None:
        try:
            path = self._prefs_path()
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            data = {}
        for user_key, lang in (data or {}).items():
            if self._allow.match(user_key) and lang in core.LANGS:
                self._store.phone(self._allow.match(user_key), lang=lang).lang = lang


# ── registration helpers ─────────────────────────────────────────────────────────────────
def check_requirements() -> bool:
    return AIOHTTP_AVAILABLE


def validate_config(config) -> bool:
    extra = getattr(config, "extra", None) or {}
    return bool(str(_setting(extra, "allowed_users", "USSD_ALLOWED_USERS", "")).strip()
                and len(str(_setting(extra, "callback_token", "USSD_CALLBACK_TOKEN", "")).strip()) >= MIN_TOKEN_LEN)


def _env_enablement() -> Optional[dict]:
    allowed = str(get_scoped_secret("USSD_ALLOWED_USERS", "") or "").strip()
    token = str(get_scoped_secret("USSD_CALLBACK_TOKEN", "") or "").strip()
    if not (allowed and token):
        return None
    # The owner's phone (first allowlisted number) is the home channel unless USSD_HOME_CHANNEL says otherwise,
    # so cron results reach the owner and the gateway does not nag about a missing home channel.
    first = core.Allowlist.parse(allowed).entries
    return seed_extra_from_env((("USSD_HOST", "host", None), ("USSD_PORT", "port", int)),
                               home_env="USSD_HOME_CHANNEL", home_default=first[0] if first else "")
