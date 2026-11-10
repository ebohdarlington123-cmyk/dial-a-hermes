"""Dial-a-Hermes USSD core: request parsing, screens, pagination, allowlist and the menu state machine.

This module has no Hermes imports so it can be unit-tested on its own. The gateway adapter
(``adapter.py``) supplies a :class:`Backend` that talks to the real Hermes gateway.

Africa's Talking USSD contract (https://developers.africastalking.com/docs/ussd/overview):
the provider POSTs ``sessionId``, ``serviceCode``, ``phoneNumber``, ``networkCode`` and ``text``
(form-encoded). ``text`` is every reply typed in this session so far, joined with ``*``
(empty on dial). The reply is ``text/plain`` and starts with ``CON `` (keep the session open
and wait for input) or ``END `` (close it).
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Optional, Protocol

DEFAULT_SCREEN_CHARS = 182  # USSD screen limit used by most African networks, prefix included
CON, END = "CON", "END"
LANGS = ("en", "pcm")

# ── Strings ──────────────────────────────────────────────────────────────────────────────
# Kept short on purpose: every character costs screen space. Pidgin (pcm) strings are listed in
# docs/PIDGIN_REVIEW.md for a native speaker to check.
STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Dial-a-Hermes",
        "needs_ok": "! Hermes needs your OK",
        "menu": "1 Ask Hermes\n2 Last reply\n3 Approvals ({n})\n4 Language\n0 Exit",
        "ask": "Type your question:",
        "empty_question": "Please type a question.",
        "thinking": "Hermes is thinking...",
        "busy": "Hermes is still working on your last question.",
        "check": "1 Check again 9 Menu",
        "no_reply": "No reply yet.",
        "approve_head": "Hermes wants to run ({p}/{n}):",
        "why": "Why",
        "approve_keys": "1 Approve 2 Deny 0 Back",
        "approve_more": "3 More 2 Deny 0 Back",
        "deny_only_keys": "2 Deny 0 Back",
        "read_first": "Read the whole command first.",
        "approved": "Approved. Hermes is continuing.",
        "denied": "Denied. Hermes will not run it.",
        "expired": "That approval has expired.",
        "no_approvals": "Nothing needs your OK.",
        "clarify_head": "Hermes asks:",
        "clarify_keys": "Pick a number, 8 Type answer, 0 Back",
        "clarify_type": "Type your answer:",
        "clarify_more": "98 More 0 Back",
        "answered": "Answer sent. Hermes is continuing.",
        "more_keys": "1 More 0 Back 9 Menu",
        "last_keys": "0 Back 9 Menu",
        "sms_key": " 7 SMS",
        "sms_sent": "Full reply sent by SMS.",
        "sms_failed": "SMS could not be sent.",
        "back_menu": "0 Back 9 Menu",
        "lang_menu": "Choose language:\n1 English\n2 Pidgin\n0 Back",
        "lang_set": "Language set to English.",
        "bye": "Bye! Dial again anytime.",
        "invalid": "Invalid choice.",
        "not_allowed": "Sorry, this number is not allowed to use this Hermes.",
        "error": "Something went wrong. Please try again.",
        "turn_failed": "Hermes could not finish that. Please try again.",
    },
    "pcm": {
        "title": "Dial-a-Hermes",
        "needs_ok": "! Hermes dey wait for your OK",
        "menu": "1 Ask Hermes\n2 Last answer\n3 Wetin need OK ({n})\n4 Language\n0 Comot",
        "ask": "Type wetin you wan ask:",
        "empty_question": "Abeg type your question.",
        "thinking": "Hermes dey think am...",
        "busy": "Hermes still dey work on your last question.",
        "check": "1 Check am again 9 Menu",
        "no_reply": "Answer never land yet.",
        "approve_head": "Hermes wan run dis ({p}/{n}):",
        "why": "Why",
        "approve_keys": "1 I gree 2 I no gree 0 Back",
        "approve_more": "3 See more 2 I no gree 0 Back",
        "deny_only_keys": "2 I no gree 0 Back",
        "read_first": "Abeg read the full command first.",
        "approved": "You don gree. Hermes dey continue.",
        "denied": "You no gree. Hermes no go run am.",
        "expired": "Dat approval don expire.",
        "no_approvals": "Nothing dey wait for your OK.",
        "clarify_head": "Hermes wan know:",
        "clarify_keys": "Pick number, 8 Type answer, 0 Back",
        "clarify_type": "Type your answer:",
        "clarify_more": "98 See more 0 Back",
        "answered": "We don send your answer. Hermes dey continue.",
        "more_keys": "1 More 0 Back 9 Menu",
        "last_keys": "0 Back 9 Menu",
        "sms_key": " 7 SMS",
        "sms_sent": "We don send the full answer for SMS.",
        "sms_failed": "SMS no fit go.",
        "back_menu": "0 Back 9 Menu",
        "lang_menu": "Choose language:\n1 English\n2 Pidgin\n0 Back",
        "lang_set": "Language don change to Pidgin.",
        "bye": "Bye-bye! Dial again anytime.",
        "invalid": "Dat option no dey.",
        "not_allowed": "Sorry, dis number no get permission to use dis Hermes.",
        "error": "Wahala don happen. Abeg try again.",
        "turn_failed": "Hermes no fit finish am. Abeg try again.",
    },
}


def tr(lang: str, key: str, **kw) -> str:
    table = STRINGS.get(lang) or STRINGS["en"]
    text = table.get(key) or STRINGS["en"][key]
    return text.format(**kw) if kw else text


# ── Africa's Talking request / response ──────────────────────────────────────────────────
@dataclass(frozen=True)
class UssdRequest:
    session_id: str
    phone: str
    service_code: str
    text: str
    network_code: str = ""

    @property
    def segments(self) -> list[str]:
        return self.text.split("*") if self.text else []


class BadRequest(ValueError):
    pass


def parse_at_request(form: dict) -> UssdRequest:
    """Parse an Africa's Talking USSD callback form (values may be lists, as from ``parse_qs``)."""
    def get(key: str) -> str:
        value = form.get(key, "")
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        return str(value or "").strip() if key != "text" else str(value or "")

    session_id, phone = get("sessionId"), get("phoneNumber")
    if not session_id or not phone:
        raise BadRequest("sessionId and phoneNumber are required")
    if len(session_id) > 128 or len(phone) > 32:
        raise BadRequest("field too long")
    text = get("text")
    if len(text) > 2000:
        raise BadRequest("text too long")
    return UssdRequest(session_id=session_id, phone=phone, service_code=get("serviceCode"),
                       text=text.strip(), network_code=get("networkCode"))


def format_response(kind: str, body: str, limit: int = DEFAULT_SCREEN_CHARS) -> str:
    """``CON``/``END`` + body, hard-capped at ``limit`` characters in total (screens are built to fit;
    the cap is a last line of defence)."""
    if kind not in (CON, END):
        raise ValueError(kind)
    out = f"{kind} {body}"
    return out if len(out) <= limit else out[: limit - 3] + "..."


# ── Text cleanup + pagination ────────────────────────────────────────────────────────────
_MD_SUBS = (
    (re.compile(r"```[a-zA-Z0-9_+-]*\n?"), ""),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"__(.+?)__", re.DOTALL), r"\1"),
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
    (re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)"), r"\1 (\2)"),
    (re.compile(r"^\s*[-*]\s+", re.MULTILINE), "- "),
)
_PUNCT = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
          "\u2026": "...", "\u00a0": " ", "\u2022": "-", "\u2192": "->", "\u20a6": "N"}


def plain_text(text: str) -> str:
    """Markdown → plain, typography → ASCII-ish, emoji dropped: feature phones render GSM-7 best."""
    text = str(text or "")
    for pattern, repl in _MD_SUBS:
        text = pattern.sub(repl, text)
    out = []
    for ch in text:
        ch = _PUNCT.get(ch, ch)
        if len(ch) == 1 and ord(ch) > 0xFFFF:
            continue  # emoji / astral symbols
        if len(ch) == 1 and (0xFE00 <= ord(ch) <= 0xFE0F or ch in "\u200d\u20e3"):
            continue  # emoji variation selectors / joiners left behind by a dropped emoji
        if len(ch) == 1 and unicodedata.category(ch) in ("So", "Cf", "Cs", "Co"):
            continue
        out.append(ch)
    text = "".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def paginate(text: str, width: int) -> list[str]:
    """Split ``text`` into pages of at most ``width`` characters, breaking at whitespace when it can.
    Always returns at least one page."""
    if width < 10:
        raise ValueError("page width too small")
    text = plain_text(text)
    if not text:
        return [""]
    pages: list[str] = []
    rest = text
    while rest:
        if len(rest) <= width:
            pages.append(rest)
            break
        cut = rest.rfind("\n", 0, width + 1)
        if cut < width // 2:
            cut = rest.rfind(" ", 0, width + 1)
        if cut < width // 2:
            cut = width  # no good break: hard split
        pages.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return pages


# ── Allowlist ────────────────────────────────────────────────────────────────────────────
def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


@dataclass
class Allowlist:
    """Phone numbers allowed to drive the agent. Empty means nobody (fail closed).

    Entries may be written ``+2348031234567``, ``2348031234567`` or local ``08031234567``
    (local numbers use ``country_code``). ``match`` returns the configured entry, so the same string
    is used as the Hermes user id that the gateway's own ``USSD_ALLOWED_USERS`` check compares."""
    entries: list[str] = field(default_factory=list)
    country_code: str = "234"

    @classmethod
    def parse(cls, raw: str, country_code: str = "234") -> "Allowlist":
        items = [p.strip() for p in re.split(r"[,\s;]+", raw or "") if p.strip()]
        return cls(entries=items, country_code=_digits(country_code) or "234")

    def _canon(self, phone: str) -> str:
        d = _digits(phone)
        if d.startswith("00"):
            d = d[2:]
        elif d.startswith("0") and self.country_code:
            d = self.country_code + d[1:]
        return d

    def match(self, phone: str) -> Optional[str]:
        want = self._canon(phone)
        if len(want) < 7:
            return None
        for entry in self.entries:
            if self._canon(entry) == want:
                return entry
        return None

    def __bool__(self) -> bool:
        return any(len(self._canon(e)) >= 7 for e in self.entries)


def redact_phone(phone: str) -> str:
    d = _digits(phone)
    return f"+***{d[-4:]}" if len(d) >= 4 else "***"


# ── Per-phone and per-session state ──────────────────────────────────────────────────────
@dataclass
class PendingApproval:
    request_id: str
    session_key: str
    command: str
    description: str
    choices: list[str]
    created: float = field(default_factory=time.time)

    @property
    def can_approve(self) -> bool:
        return "once" in self.choices


@dataclass
class PendingClarify:
    clarify_id: str
    question: str
    choices: list[str]
    created: float = field(default_factory=time.time)


@dataclass
class PhoneState:
    user_key: str
    lang: str = "en"
    turn_active: bool = False
    turn_started: float = 0.0
    reply_parts: list[str] = field(default_factory=list)
    last_reply: str = ""
    last_failed: bool = False
    sms_sent_for_last: bool = False
    approvals: list[PendingApproval] = field(default_factory=list)
    clarify: Optional[PendingClarify] = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)

    def touch(self) -> None:
        self.changed.set()


# Screens
MAIN, ASK, WAIT, READ, APPROVAL, LANG, CLARIFY, CLARIFY_TEXT, INFO = (
    "main", "ask", "wait", "read", "approval", "lang", "clarify", "clarify_text", "info")


@dataclass
class UssdSession:
    session_id: str
    user_key: str
    screen: str = MAIN
    page: int = 0
    consumed: int = 0
    note: str = ""
    updated: float = field(default_factory=time.time)


class Backend(Protocol):
    sms_enabled: bool

    async def submit(self, state: PhoneState, text: str) -> None: ...
    async def resolve_approval(self, state: PhoneState, approval: PendingApproval, choice: str) -> bool: ...
    async def resolve_clarify(self, state: PhoneState, clarify: PendingClarify, response: str) -> bool: ...
    async def send_sms(self, state: PhoneState, text: str) -> bool: ...
    def save_lang(self, state: PhoneState) -> None: ...


class StateStore:
    """In-memory per-phone and per-USSD-session state (sessions expire; phones persist)."""

    def __init__(self, default_lang: str = "en", session_ttl: float = 600.0):
        self.default_lang = default_lang if default_lang in LANGS else "en"
        self.session_ttl = session_ttl
        self.phones: dict[str, PhoneState] = {}
        self.sessions: dict[str, UssdSession] = {}

    def phone(self, user_key: str, lang: Optional[str] = None) -> PhoneState:
        st = self.phones.get(user_key)
        if st is None:
            st = self.phones[user_key] = PhoneState(user_key=user_key, lang=lang or self.default_lang)
        return st

    def session(self, session_id: str, user_key: str) -> UssdSession:
        now = time.time()
        for sid in [s for s, v in self.sessions.items() if now - v.updated > self.session_ttl]:
            self.sessions.pop(sid, None)
        ses = self.sessions.get(session_id)
        if ses is None or ses.user_key != user_key:
            ses = self.sessions[session_id] = UssdSession(session_id=session_id, user_key=user_key)
        ses.updated = now
        return ses


# ── The menu state machine ───────────────────────────────────────────────────────────────
class UssdEngine:
    def __init__(self, store: StateStore, backend: Backend, *, screen_chars: int = DEFAULT_SCREEN_CHARS,
                 reply_wait: float = 6.0, turn_timeout: float = 900.0):
        self.store = store
        self.backend = backend
        self.screen_chars = max(60, int(screen_chars))
        self.reply_wait = max(0.0, float(reply_wait))
        self.turn_timeout = turn_timeout

    # body budget: the whole response (prefix + body) must fit the screen
    @property
    def body_chars(self) -> int:
        return self.screen_chars - 4

    async def handle(self, req: UssdRequest, user_key: str) -> str:
        state = self.store.phone(user_key)
        ses = self.store.session(req.session_id, user_key)
        segs = req.segments
        if ses.consumed > len(segs):  # provider restarted the history: start over
            ses.screen, ses.page, ses.consumed = MAIN, 0, 0
        new = segs[ses.consumed:]
        ses.consumed = len(segs)
        self._expire_turn(state)
        if not new:
            if req.text == "" and ses.screen != MAIN:
                ses.screen, ses.page = MAIN, 0
            return await self.render(ses, state)
        # ASK / free-text screens swallow the rest of the input verbatim ('*' is part of the text)
        result: Optional[str] = None
        i = 0
        while i < len(new):
            if ses.screen in (ASK, CLARIFY_TEXT):
                result = await self._on_free_text(ses, state, "*".join(new[i:]))
                break
            result = await self._on_key(ses, state, new[i].strip())
            i += 1
            if result is not None and result.startswith(END):
                return result
        return result if result is not None else await self.render(ses, state)

    def _expire_turn(self, state: PhoneState) -> None:
        if state.turn_active and self.turn_timeout and time.time() - state.turn_started > self.turn_timeout:
            state.turn_active = False
            state.last_reply = "\n\n".join(state.reply_parts) or tr(state.lang, "turn_failed")
            state.reply_parts = []

    # ── input handling ──
    async def _on_free_text(self, ses: UssdSession, state: PhoneState, text: str) -> Optional[str]:
        text = text.strip()
        lang = state.lang
        if ses.screen == ASK:
            if text in ("0", "9", ""):
                ses.screen, ses.page = MAIN, 0
                ses.note = tr(lang, "empty_question") if text == "" else ""
                return None
            if state.turn_active:
                ses.screen, ses.note = WAIT, tr(lang, "busy")
                return await self._wait_screen(ses, state, wait=False)
            await self.backend.submit(state, text)
            ses.screen, ses.page, ses.note = WAIT, 0, ""
            return await self._wait_screen(ses, state, wait=True)
        # CLARIFY_TEXT
        clar = state.clarify
        if text in ("0", ""):
            ses.screen = CLARIFY if clar else MAIN
            return None
        if clar is None:
            ses.screen, ses.note = MAIN, tr(lang, "expired")
            return None
        ok = await self.backend.resolve_clarify(state, clar, text)
        state.clarify = None
        ses.screen, ses.note = WAIT, tr(lang, "answered" if ok else "expired")
        return await self._wait_screen(ses, state, wait=True)

    async def _on_key(self, ses: UssdSession, state: PhoneState, key: str) -> Optional[str]:
        lang = state.lang
        ses.note = ""
        scr = ses.screen
        if key == "9" and scr not in (CLARIFY,):
            ses.screen, ses.page = MAIN, 0
            return None
        if scr == MAIN:
            if key == "1":
                if state.turn_active:
                    ses.screen, ses.note = WAIT, tr(lang, "busy")
                    return await self._wait_screen(ses, state, wait=False)
                ses.screen = ASK
            elif key == "2":
                return await self._show_reply(ses, state)
            elif key == "3":
                return self._go_approvals(ses, state)
            elif key == "4":
                ses.screen = LANG
            elif key == "0":
                return format_response(END, tr(lang, "bye"), self.screen_chars)
            else:
                ses.note = tr(lang, "invalid")
            return None
        if scr == LANG:
            if key in ("1", "2"):
                state.lang = "en" if key == "1" else "pcm"
                self.backend.save_lang(state)
                ses.screen, ses.note = MAIN, tr(state.lang, "lang_set")
            elif key == "0":
                ses.screen = MAIN
            else:
                ses.note = tr(lang, "invalid")
            return None
        if scr in (WAIT, INFO):
            if key == "1" and scr == WAIT:
                return await self._wait_screen(ses, state, wait=True)
            if key == "0":
                ses.screen = MAIN
                return None
            ses.note = tr(lang, "invalid")
            return None
        if scr == READ:
            pages = self._reply_pages(state)
            if key == "1" and ses.page < len(pages) - 1:
                ses.page += 1
            elif key == "0":
                if ses.page > 0:
                    ses.page -= 1
                else:
                    ses.screen = MAIN
            elif key == "7" and self.backend.sms_enabled and state.last_reply:
                ok = await self.backend.send_sms(state, state.last_reply)
                state.sms_sent_for_last = state.sms_sent_for_last or ok
                ses.note = tr(lang, "sms_sent" if ok else "sms_failed")
            else:
                ses.note = tr(lang, "invalid")
            return None
        if scr == APPROVAL:
            appr = state.approvals[0] if state.approvals else None
            if appr is None:
                ses.screen, ses.note = MAIN, tr(lang, "no_approvals")
                return None
            pages = self._approval_pages(state, appr)
            last = ses.page >= len(pages) - 1
            if key == "3" and not last:
                ses.page += 1
            elif key == "1":
                if not appr.can_approve:
                    ses.note = tr(lang, "invalid")
                elif not last:
                    ses.note = tr(lang, "read_first")
                else:
                    return await self._settle(ses, state, appr, "once")
            elif key == "2":
                return await self._settle(ses, state, appr, "deny")
            elif key == "0":
                if ses.page > 0:
                    ses.page -= 1
                else:
                    ses.screen, ses.page = MAIN, 0
            else:
                ses.note = tr(lang, "invalid")
            return None
        if scr == CLARIFY:
            clar = state.clarify
            if clar is None:
                ses.screen, ses.note = MAIN, tr(lang, "expired")
                return None
            pages = self._clarify_pages(state, clar)
            last = ses.page >= len(pages) - 1
            if key == "98" and not last:
                ses.page += 1
            elif key == "0":
                if ses.page > 0:
                    ses.page -= 1
                else:
                    ses.screen, ses.page = MAIN, 0
            elif key == "8":
                ses.screen = CLARIFY_TEXT
            elif key == "9" and not (clar.choices and len(clar.choices) >= 9):
                ses.screen, ses.page = MAIN, 0
            elif key.isdigit() and clar.choices and 1 <= int(key) <= min(len(clar.choices), 7):
                ok = await self.backend.resolve_clarify(state, clar, clar.choices[int(key) - 1])
                state.clarify = None
                ses.screen, ses.page, ses.note = WAIT, 0, tr(lang, "answered" if ok else "expired")
                return await self._wait_screen(ses, state, wait=True)
            else:
                ses.note = tr(lang, "invalid")
            return None
        ses.screen = MAIN
        return None

    async def _settle(self, ses: UssdSession, state: PhoneState, appr: PendingApproval, choice: str) -> str:
        ok = await self.backend.resolve_approval(state, appr, choice)
        if appr in state.approvals:
            state.approvals.remove(appr)
        state.touch()
        ses.page = 0
        if not ok:
            ses.screen, ses.note = WAIT, tr(state.lang, "expired")
        else:
            ses.screen, ses.note = WAIT, tr(state.lang, "approved" if choice != "deny" else "denied")
        return await self._wait_screen(ses, state, wait=True)

    def _go_approvals(self, ses: UssdSession, state: PhoneState) -> Optional[str]:
        if state.approvals:
            ses.screen, ses.page = APPROVAL, 0
        else:
            ses.screen, ses.note = INFO, tr(state.lang, "no_approvals")
        return None

    async def _show_reply(self, ses: UssdSession, state: PhoneState) -> Optional[str]:
        if state.turn_active and not state.last_reply:
            ses.screen = WAIT
            return await self._wait_screen(ses, state, wait=False)
        if not state.last_reply:
            ses.screen, ses.note = INFO, tr(state.lang, "no_reply")
            return None
        ses.screen, ses.page = READ, 0
        return None

    async def _wait_screen(self, ses: UssdSession, state: PhoneState, *, wait: bool) -> str:
        """Wait (bounded by the USSD response window) until the turn finishes or needs the user."""
        if wait and state.turn_active and not state.approvals and not state.clarify and self.reply_wait > 0:
            deadline = time.monotonic() + self.reply_wait
            while state.turn_active and not state.approvals and not state.clarify:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                state.changed.clear()
                try:
                    await asyncio.wait_for(state.changed.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
        if state.approvals:
            ses.screen, ses.page = APPROVAL, 0
        elif state.clarify:
            ses.screen, ses.page = CLARIFY, 0
        elif not state.turn_active and state.last_reply:
            ses.screen, ses.page = READ, 0
        else:
            ses.screen = WAIT
        return await self.render(ses, state)

    # ── rendering ──
    def _reply_pages(self, state: PhoneState) -> list[str]:
        footer = max(len(tr(state.lang, "more_keys")),
                     len(tr(state.lang, "last_keys")) + len(tr(state.lang, "sms_key"))) + 1
        return paginate(state.last_reply or tr(state.lang, "no_reply"), self.body_chars - footer - 8)

    def _approval_pages(self, state: PhoneState, appr: PendingApproval) -> list[str]:
        lang = state.lang
        body = f"{appr.command}\n{tr(lang, 'why')}: {appr.description}"
        head = len(tr(lang, "approve_head", p=99, n=99)) + 1
        footer = max(len(tr(lang, "approve_keys")), len(tr(lang, "approve_more"))) + 1
        # commands keep their exact characters: only whitespace runs are folded by plain_text,
        # so paginate the raw text without markdown stripping
        note = max(len(tr(lang, k)) for k in ("read_first", "invalid", "expired")) + 1  # room for a warning
        width = self.body_chars - head - footer - note - 1
        return _paginate_raw(body, width)

    def _clarify_pages(self, state: PhoneState, clar: PendingClarify) -> list[str]:
        lang = state.lang
        lines = [clar.question] + [f"{i}. {c}" for i, c in enumerate(clar.choices[:7], start=1)]
        head = len(tr(lang, "clarify_head")) + 1
        footer = max(len(tr(lang, "clarify_keys")), len(tr(lang, "clarify_more"))) + 1
        return paginate("\n".join(lines), self.body_chars - head - footer)

    async def render(self, ses: UssdSession, state: PhoneState) -> str:
        lang = state.lang
        note = (ses.note + "\n") if ses.note else ""
        scr = ses.screen
        if scr == MAIN:
            head = tr(lang, "title") + ("\n" + tr(lang, "needs_ok") if state.approvals else "")
            body = f"{note}{head}\n{tr(lang, 'menu', n=len(state.approvals))}"
        elif scr == ASK:
            body = f"{note}{tr(lang, 'ask')}"
        elif scr == LANG:
            body = f"{note}{tr(lang, 'lang_menu')}"
        elif scr == WAIT:
            msg = ses.note or tr(lang, "thinking")
            if ses.note and state.turn_active and ses.note != tr(lang, "busy"):
                msg = f"{ses.note}\n{tr(lang, 'thinking')}"
            body = f"{msg}\n{tr(lang, 'check')}"
        elif scr == INFO:
            body = f"{ses.note}\n{tr(lang, 'back_menu')}"
        elif scr == READ:
            pages = self._reply_pages(state)
            ses.page = min(ses.page, len(pages) - 1)
            last = ses.page == len(pages) - 1
            if last:
                keys = tr(lang, "last_keys")
                if self.backend.sms_enabled and state.last_reply and not state.sms_sent_for_last:
                    keys += tr(lang, "sms_key")
            else:
                keys = tr(lang, "more_keys")
            prefix = f"({ses.page + 1}/{len(pages)}) " if len(pages) > 1 else ""
            body = f"{prefix}{pages[ses.page]}\n{keys}"
            if ses.note:
                body = f"{ses.note}\n{body}"
        elif scr == APPROVAL:
            appr = state.approvals[0] if state.approvals else None
            if appr is None:
                ses.screen, ses.note = INFO, tr(lang, "no_approvals")
                return await self.render(ses, state)
            pages = self._approval_pages(state, appr)
            ses.page = min(ses.page, len(pages) - 1)
            last = ses.page == len(pages) - 1
            if not appr.can_approve:
                keys = tr(lang, "deny_only_keys") if last else tr(lang, "approve_more")
            else:
                keys = tr(lang, "approve_keys") if last else tr(lang, "approve_more")
            head = tr(lang, "approve_head", p=ses.page + 1, n=len(pages))
            body = f"{note}{head}\n{pages[ses.page]}\n{keys}"
        elif scr == CLARIFY:
            clar = state.clarify
            if clar is None:
                ses.screen = MAIN
                return await self.render(ses, state)
            pages = self._clarify_pages(state, clar)
            ses.page = min(ses.page, len(pages) - 1)
            last = ses.page == len(pages) - 1
            keys = tr(lang, "clarify_keys") if last else tr(lang, "clarify_more")
            body = f"{note}{tr(lang, 'clarify_head')}\n{pages[ses.page]}\n{keys}"
        elif scr == CLARIFY_TEXT:
            body = f"{note}{tr(lang, 'clarify_type')}"
        else:
            ses.screen = MAIN
            return await self.render(ses, state)
        if len(body) > self.body_chars and ses.note:
            ses.note = ""  # a note never pushes the keys off the screen
            return await self.render(ses, state)
        return format_response(CON, body, self.screen_chars)


def _paginate_raw(text: str, width: int) -> list[str]:
    """Like :func:`paginate` but keeps every character of the text (used for commands the user approves)."""
    width = max(10, width)
    text = str(text or "").strip() or "-"
    pages, rest = [], text
    while rest:
        if len(rest) <= width:
            pages.append(rest)
            break
        cut = rest.rfind(" ", 0, width + 1)
        if cut < width // 2:
            cut = width
        pages.append(rest[:cut])
        rest = rest[cut:]
    return pages
