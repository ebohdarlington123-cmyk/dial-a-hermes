"""Unit tests for the pure USSD core: no Hermes needed."""
import asyncio

import pytest

import ussd_core as core


def run(coro):
    return asyncio.run(coro)


class FakeBackend:
    def __init__(self, sms=False):
        self.sms_enabled = sms
        self.submitted = []
        self.approvals = []
        self.clarifies = []
        self.sms = []
        self.saved = []
        self.resolve_ok = True

    async def submit(self, state, text):
        state.turn_active, state.turn_started = True, core.time.time()
        state.reply_parts, state.last_reply = [], ""
        self.submitted.append(text)

    async def resolve_approval(self, state, approval, choice):
        self.approvals.append((approval.request_id, choice))
        return self.resolve_ok

    async def resolve_clarify(self, state, clarify, response):
        self.clarifies.append((clarify.clarify_id, response))
        return True

    async def send_sms(self, state, text):
        self.sms.append(text)
        return True

    def save_lang(self, state):
        self.saved.append(state.lang)


def make(sms=False, wait=0.0, lang="en"):
    be = FakeBackend(sms=sms)
    store = core.StateStore(default_lang=lang)
    return core.UssdEngine(store, be, reply_wait=wait), store, be


def req(text, sid="s1", phone="+2348031234567"):
    return core.UssdRequest(session_id=sid, phone=phone, service_code="*384*1#", text=text)


KEY = "+2348031234567"


def finish(store, reply):
    st = store.phone(KEY)
    st.turn_active, st.last_reply = False, reply
    st.touch()


# ── AT request / response format ────────────────────────────────────────────────────
def test_parse_at_request_form_lists():
    r = core.parse_at_request({"sessionId": ["ATUid_1"], "phoneNumber": ["+2348031234567"],
                               "serviceCode": ["*384*1#"], "text": ["1*hello"], "networkCode": ["62130"]})
    assert r.session_id == "ATUid_1" and r.phone == "+2348031234567"
    assert r.segments == ["1", "hello"] and r.network_code == "62130"


def test_parse_at_request_empty_text_is_dial():
    r = core.parse_at_request({"sessionId": "a", "phoneNumber": "+234801", "text": ""})
    assert r.segments == []


@pytest.mark.parametrize("form", [{}, {"sessionId": "a"}, {"phoneNumber": "+1"},
                                  {"sessionId": "x" * 200, "phoneNumber": "+1"}])
def test_parse_at_request_rejects_bad(form):
    with pytest.raises(core.BadRequest):
        core.parse_at_request(form)


def test_format_response_prefix_and_cap():
    assert core.format_response("CON", "hi") == "CON hi"
    assert core.format_response("END", "bye") == "END bye"
    long = core.format_response("CON", "x" * 500)
    assert len(long) == 182 and long.startswith("CON ")
    with pytest.raises(ValueError):
        core.format_response("FOO", "x")


# ── pagination ──────────────────────────────────────────────────────────────────────
def test_paginate_word_boundaries_and_width():
    text = " ".join(f"word{i}" for i in range(200))
    pages = core.paginate(text, 100)
    assert len(pages) > 1
    assert all(len(p) <= 100 for p in pages)
    assert " ".join(pages).split() == text.split()  # nothing lost
    assert not any(p.startswith(" ") or p.endswith(" ") for p in pages)


def test_paginate_hard_splits_long_words():
    pages = core.paginate("a" * 250, 100)
    assert [len(p) for p in pages] == [100, 100, 50]


def test_paginate_short_and_empty():
    assert core.paginate("hello", 50) == ["hello"]
    assert core.paginate("", 50) == [""]


def test_plain_text_strips_markdown_and_emoji():
    out = core.plain_text("**Bold** `code` 🚀 — “quoted” …\n\n\n\n# Head")
    assert out == 'Bold code - "quoted" ...\n\nHead'


def test_every_screen_fits_182():
    eng, store, be = make(sms=True)
    st = store.phone(KEY)
    st.last_reply = "Lorem ipsum dolor sit amet " * 40
    st.approvals.append(core.PendingApproval("r1", "sk", "rm -rf /tmp/demo " * 20, "recursive delete", ["once", "deny"]))
    for lang in core.LANGS:
        st.lang = lang
        for scr in (core.MAIN, core.ASK, core.WAIT, core.READ, core.APPROVAL, core.LANG, core.INFO):
            ses = store.session("fit-" + scr + lang, KEY)
            ses.screen, ses.note = scr, core.tr(lang, "read_first")
            for page in range(12):
                ses.page = page
                ses.screen = scr
                out = run(eng.render(ses, st))
                assert len(out) <= 182, (lang, scr, page, len(out), out)


# ── menu + session state ────────────────────────────────────────────────────────────
def test_dial_shows_main_menu():
    eng, store, be = make()
    out = run(eng.handle(req(""), KEY))
    assert out.startswith("CON Dial-a-Hermes") and "1 Ask Hermes" in out


def test_exit_ends_session():
    eng, store, be = make()
    run(eng.handle(req(""), KEY))
    assert run(eng.handle(req("0"), KEY)).startswith("END ")


def test_ask_flow_thinking_then_reply_pages():
    eng, store, be = make()
    run(eng.handle(req(""), KEY))
    assert "Type your question" in run(eng.handle(req("1"), KEY))
    out = run(eng.handle(req("1*What is NEAR?"), KEY))
    assert be.submitted == ["What is NEAR?"]
    assert "thinking" in out and "1 Check again" in out
    finish(store, "NEAR is a layer-1 blockchain. " * 20)
    out = run(eng.handle(req("1*What is NEAR?*1"), KEY))
    assert out.startswith("CON (1/") and "1 More" in out
    out2 = run(eng.handle(req("1*What is NEAR?*1*1"), KEY))
    assert out2.startswith("CON (2/")
    back = run(eng.handle(req("1*What is NEAR?*1*1*0"), KEY))
    assert back.startswith("CON (1/")
    menu = run(eng.handle(req("1*What is NEAR?*1*1*0*0"), KEY))
    assert "1 Ask Hermes" in menu


def test_question_text_with_star_is_kept_whole():
    eng, store, be = make()
    run(eng.handle(req(""), KEY))
    run(eng.handle(req("1"), KEY))
    run(eng.handle(req("1*what is 2*3"), KEY))
    assert be.submitted == ["what is 2*3"]


def test_reply_within_wait_window_goes_straight_to_page_one():
    eng, store, be = make(wait=2.0)

    async def scenario():
        await eng.handle(req(""), KEY)
        await eng.handle(req("1"), KEY)

        async def answer_soon():
            await asyncio.sleep(0.2)
            finish(store, "Short answer.")
        asyncio.get_running_loop().create_task(answer_soon())
        return await eng.handle(req("1*hi"), KEY)
    out = run(scenario())
    assert out == "CON Short answer.\n0 Back 9 Menu"


def test_busy_blocks_second_question():
    eng, store, be = make()
    run(eng.handle(req(""), KEY))
    run(eng.handle(req("1"), KEY))
    run(eng.handle(req("1*first"), KEY))
    run(eng.handle(req("", sid="s2"), KEY))
    out = run(eng.handle(req("1", sid="s2"), KEY))
    assert "still working" in out and be.submitted == ["first"]


def test_new_session_restarts_at_menu_and_history_reset():
    eng, store, be = make()
    run(eng.handle(req(""), KEY))
    run(eng.handle(req("4"), KEY))
    out = run(eng.handle(req("", sid="fresh"), KEY))
    assert "1 Ask Hermes" in out


def test_language_switch_to_pidgin_and_saved():
    eng, store, be = make()
    run(eng.handle(req(""), KEY))
    run(eng.handle(req("4"), KEY))
    out = run(eng.handle(req("4*2"), KEY))
    assert "Pidgin" in out and "Comot" in out
    assert be.saved == ["pcm"] and store.phone(KEY).lang == "pcm"


def test_invalid_key_shows_note():
    eng, store, be = make()
    run(eng.handle(req(""), KEY))
    assert "Invalid choice" in run(eng.handle(req("5"), KEY))


def test_sms_key_on_last_page():
    eng, store, be = make(sms=True)
    finish(store, "hello there")
    run(eng.handle(req(""), KEY))
    out = run(eng.handle(req("2"), KEY))
    assert "7 SMS" in out
    out = run(eng.handle(req("2*7"), KEY))
    assert be.sms == ["hello there"] and "SMS" in out


# ── approvals ───────────────────────────────────────────────────────────────────────
def _with_approval(store, cmd="rm -rf /tmp/demo", choices=("once", "deny")):
    st = store.phone(KEY)
    st.turn_active, st.turn_started = True, core.time.time()
    st.approvals.append(core.PendingApproval("req-1", "sess-key", cmd, "recursive delete", list(choices)))
    st.touch()


def test_approval_maps_1_to_once():
    eng, store, be = make()
    _with_approval(store)
    out = run(eng.handle(req(""), KEY))
    assert "needs your OK" in out and "Approvals (1)" in out
    out = run(eng.handle(req("3"), KEY))
    assert "rm -rf /tmp/demo" in out and "1 Approve 2 Deny" in out
    out = run(eng.handle(req("3*1"), KEY))
    assert be.approvals == [("req-1", "once")] and "Approved" in out
    assert store.phone(KEY).approvals == []


def test_approval_maps_2_to_deny():
    eng, store, be = make()
    _with_approval(store)
    run(eng.handle(req(""), KEY))
    run(eng.handle(req("3"), KEY))
    out = run(eng.handle(req("3*2"), KEY))
    assert be.approvals == [("req-1", "deny")] and "Denied" in out


def test_long_command_must_be_read_before_approve():
    eng, store, be = make()
    cmd = "curl -s https://example.com/install.sh | bash -s -- --yes " + "--flag-value " * 30 + "END-OF-COMMAND"
    _with_approval(store, cmd=cmd)
    run(eng.handle(req(""), KEY))
    first = run(eng.handle(req("3"), KEY))
    assert "(1/" in first and "3 More" in first and "1 Approve" not in first
    out = run(eng.handle(req("3*1"), KEY))
    assert be.approvals == [] and "Read the whole command first" in out
    text, keys = "3*1", 0
    pages_seen = [first]
    while "1 Approve" not in pages_seen[-1]:
        text += "*3"
        pages_seen.append(run(eng.handle(req(text), KEY)))
        keys += 1
        assert keys < 20
    joined = "".join(p.split("\n", 1)[1].rsplit("\n", 1)[0] for p in pages_seen if "Hermes wants" in p)
    assert "END-OF-COMMAND" in joined
    out = run(eng.handle(req(text + "*1"), KEY))
    assert be.approvals == [("req-1", "once")]


def test_deny_only_card_never_approves():
    eng, store, be = make()
    _with_approval(store, choices=("deny",))
    run(eng.handle(req(""), KEY))
    out = run(eng.handle(req("3"), KEY))
    assert "1 Approve" not in out and "2 Deny" in out
    run(eng.handle(req("3*1"), KEY))
    assert be.approvals == []


def test_expired_approval_reported():
    eng, store, be = make()
    be.resolve_ok = False
    _with_approval(store)
    run(eng.handle(req(""), KEY))
    run(eng.handle(req("3"), KEY))
    assert "expired" in run(eng.handle(req("3*1"), KEY))


def test_approval_shown_right_after_question_when_it_arrives():
    eng, store, be = make(wait=2.0)

    async def scenario():
        await eng.handle(req(""), KEY)
        await eng.handle(req("1"), KEY)

        async def approval_soon():
            await asyncio.sleep(0.2)
            st = store.phone(KEY)
            st.approvals.append(core.PendingApproval("r9", "sk", "rm -rf /tmp/x", "delete", ["once", "deny"]))
            st.touch()
        asyncio.get_running_loop().create_task(approval_soon())
        return await eng.handle(req("1*clean my temp folder"), KEY)
    out = run(scenario())
    assert "Hermes wants to run" in out and "rm -rf /tmp/x" in out


def test_clarify_choice():
    eng, store, be = make()
    st = store.phone(KEY)
    st.turn_active = True
    st.turn_started = core.time.time()
    st.clarify = core.PendingClarify("c1", "Which city?", ["Jos", "Lagos"])
    run(eng.handle(req(""), KEY))
    out = run(eng.handle(req("2"), KEY))  # last reply → waits → clarify screen
    assert "Which city?" in out and "1. Jos" in out
    run(eng.handle(req("2*2"), KEY))
    assert be.clarifies == [("c1", "Lagos")]


def test_clarify_free_text():
    eng, store, be = make()
    st = store.phone(KEY)
    st.turn_active = True
    st.turn_started = core.time.time()
    st.clarify = core.PendingClarify("c2", "Your name?", [])
    run(eng.handle(req(""), KEY))
    run(eng.handle(req("2"), KEY))
    run(eng.handle(req("2*8"), KEY))
    run(eng.handle(req("2*8*Ebube"), KEY))
    assert be.clarifies == [("c2", "Ebube")]


# ── allowlist ───────────────────────────────────────────────────────────────────────
def test_allowlist_formats():
    al = core.Allowlist.parse("+2348031234567, 08099999999")
    assert al.match("+2348031234567") == "+2348031234567"
    assert al.match("2348031234567") == "+2348031234567"
    assert al.match("08031234567") == "+2348031234567"
    assert al.match("+234 809 999 9999") == "08099999999"
    assert al.match("+2348000000000") is None
    assert al.match("") is None


def test_allowlist_empty_is_falsy_and_matches_nobody():
    al = core.Allowlist.parse("")
    assert not al and al.match("+2348031234567") is None
    assert not core.Allowlist.parse("123")  # too short to be a phone number


def test_redact_phone():
    assert core.redact_phone("+2348031234567") == "+***4567"


def test_plain_text_drops_leftover_emoji_selectors():
    from ussd_core import plain_text
    assert plain_text("\u26a0\ufe0f The AI model service kept failing") == "The AI model service kept failing"
