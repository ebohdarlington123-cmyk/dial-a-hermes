# Pidgin strings: please review

Ebube, please read every Pidgin line below as a Naija speaker would. Mark anything that sounds
stiff, wrong or "translated". Put the better wording in the last column, or edit the files directly:

* Keypad menus: `plugins/dial-a-hermes/ussd_core.py` → `STRINGS["pcm"]`. **Each screen must stay ≤182
  characters**, so keep fixes about as short; `pytest tests/test_core.py` checks this.
* Hermes's own messages: `plugins/hermes-lang-pcm/locales/pcm.yaml`. Keep `{placeholders}`,
  `/commands` and anything in backticks exactly as they are; run `hermes plugins validate plugins/hermes-lang-pcm`.

Choices I made that you might want to change:
* "I gree" / "I no gree" for Approve / Deny; "Comot" for Exit; "Wetin need OK" for the Approvals menu.
* Spelling: "dis/dat/wetin/dey/don/abeg" (common written Naija), not "this/that".
* Approval inputs accept the English words **plus** `gree, i gree, oya, do am` (yes) and
  `no gree, i no gree, no do am, leave am` (no). Is "oya" too loose for "yes, run it"? It can be removed.
* "Language" and "Pidgin" in the language menu stay in English, so both groups can find them.

## 1. Keypad menus (USSD screens)

| key | English | Pidgin | your fix |
|---|---|---|---|
| `needs_ok` | ! Hermes needs your OK | ! Hermes dey wait for your OK |  |
| `menu` | 1 Ask Hermes / 2 Last reply / 3 Approvals ({n}) / 4 Language / 0 Exit | 1 Ask Hermes / 2 Last answer / 3 Wetin need OK ({n}) / 4 Language / 0 Comot |  |
| `ask` | Type your question: | Type wetin you wan ask: |  |
| `empty_question` | Please type a question. | Abeg type your question. |  |
| `thinking` | Hermes is thinking... | Hermes dey think am... |  |
| `busy` | Hermes is still working on your last question. | Hermes still dey work on your last question. |  |
| `check` | 1 Check again 9 Menu | 1 Check am again 9 Menu |  |
| `no_reply` | No reply yet. | Answer never land yet. |  |
| `approve_head` | Hermes wants to run ({p}/{n}): | Hermes wan run dis ({p}/{n}): |  |
| `approve_keys` | 1 Approve 2 Deny 0 Back | 1 I gree 2 I no gree 0 Back |  |
| `approve_more` | 3 More 2 Deny 0 Back | 3 See more 2 I no gree 0 Back |  |
| `deny_only_keys` | 2 Deny 0 Back | 2 I no gree 0 Back |  |
| `read_first` | Read the whole command first. | Abeg read the full command first. |  |
| `approved` | Approved. Hermes is continuing. | You don gree. Hermes dey continue. |  |
| `denied` | Denied. Hermes will not run it. | You no gree. Hermes no go run am. |  |
| `expired` | That approval has expired. | Dat approval don expire. |  |
| `no_approvals` | Nothing needs your OK. | Nothing dey wait for your OK. |  |
| `clarify_head` | Hermes asks: | Hermes wan know: |  |
| `clarify_keys` | Pick a number, 8 Type answer, 0 Back | Pick number, 8 Type answer, 0 Back |  |
| `clarify_more` | 98 More 0 Back | 98 See more 0 Back |  |
| `answered` | Answer sent. Hermes is continuing. | We don send your answer. Hermes dey continue. |  |
| `sms_sent` | Full reply sent by SMS. | We don send the full answer for SMS. |  |
| `sms_failed` | SMS could not be sent. | SMS no fit go. |  |
| `lang_set` | Language set to English. | Language don change to Pidgin. |  |
| `bye` | Bye! Dial again anytime. | Bye-bye! Dial again anytime. |  |
| `invalid` | Invalid choice. | Dat option no dey. |  |
| `not_allowed` | Sorry, this number is not allowed to use this Hermes. | Sorry, dis number no get permission to use dis Hermes. |  |
| `error` | Something went wrong. Please try again. | Wahala don happen. Abeg try again. |  |
| `turn_failed` | Hermes could not finish that. Please try again. | Hermes no fit finish am. Abeg try again. |  |

(Keys with identical text in both languages, like `0 Back 9 Menu` and `7 SMS`, are left out.)

## 2. Hermes language pack (`hermes-lang-pcm`), 85 strings

```yaml
# Nigerian Pidgin (pcm). Partial pack: keys not here fall back to English.
# Rule: {placeholders}, `code`, /commands and config keys stay exactly as in English.
approval:
  dangerous_header: "⚠️  DIS COMMAND FIT CAUSE WAHALA: {description}"
  choose_long: "      [o]nce  |  [s]ession  |  [a]lways  |  [d]eny"
  choose_short: "      [o]nce  |  [s]ession  |  [d]eny"
  prompt_long: "      Wetin you choose [o/s/a/D]: "
  prompt_short: "      Wetin you choose [o/s/D]: "
  choose_smart_deny: "      [o]nce  |  [d]eny"
  prompt_smart_deny: "      Wetin you choose [o/D]: "
  timeout: "      ⏱ You no answer after {waited}, so the command no run. Ask again if you still want am, or make the time long: hermes config set approvals.timeout {suggested}"
  allowed_once: "      ✓ I don allow am dis one time"
  allowed_session: "      ✓ I don allow am for dis session"
  allowed_always: "      ✓ I don add am to the list wey dey always allowed"
  denied: "      ✗ I no gree"
  cancelled: "      ✗ E don cancel"
  blocklist_message: "Dis command dey the list wey dem no dey ever allow. Nobody fit approve am."
  inputs:
    approve: "approve,yes,ok,okay,confirm,y,gree,i gree,oya,do am"
    deny: "deny,no,reject,cancel,n,no gree,i no gree,no do am,leave am"
    always: "always,approve always,always approve,every time"
    session: "session,approve session,session approve"
    confirm_once: "approve,approve once,once,yes,ok,confirm,gree,i gree,oya"
    confirm_always: "always,always approve,remember,every time"
    confirm_cancel: "cancel,nevermind,no,deny,no gree,leave am"
  summary:
    denied: "You no gree for dis {noun}, so e no run."
    timeout: "You no answer inside {window}, so the {noun} no run."
    notify_failed: "The approval request no fit reach you, so the {noun} no run."
    cancelled: "The approval question don comot or e no reach you, so the {noun} no run."
    blocked: "Dem no dey allow dis {noun} when nobody dey watch, so e no run."
    default: "Dis {noun} no run."
  noun:
    command: "command"
    code: "code"
    action: "action"
  window:
    hour_one: "{count} hour"
    hour_other: "{count} hours"
    minute_one: "{count} minute"
    minute_other: "{count} minutes"
    second_one: "{count} second"
    second_other: "{count} seconds"

gateway:
  approval_expired: "⚠️ The approval don expire (the agent no dey wait again). Tell the agent make e try again."
  approve:
    no_pending: "No command dey wait for approval."
    once_singular: "✅ You don approve the command. The agent dey continue..."
    once_plural: "✅ You don approve the commands ({count} commands). The agent dey continue..."
    session_singular: "✅ You don approve the command (dis kain command go pass for dis session). The agent dey continue..."
    session_plural: "✅ You don approve the commands (dis kain command go pass for dis session) ({count} commands). The agent dey continue..."
    always_singular: "✅ You don approve the command (dis kain command go always pass). The agent dey continue..."
    always_plural: "✅ You don approve the commands (dis kain command go always pass) ({count} commands). The agent dey continue..."
  deny:
    stale: "❌ Command no run (the approval don old)."
    no_pending: "No command dey wait make you deny am."
    denied_singular: "❌ You no gree, the command no run."
    denied_plural: "❌ You no gree, the commands no run ({count} commands)."
    denied_reason_singular: "❌ You no gree, the command no run. I don tell the agent why: \"{reason}\""
    denied_reason_plural: "❌ You no gree, the commands no run ({count} commands). I don tell the agent why: \"{reason}\""
  stop:
    stopped_pending: "⚡ I don stop am. The agent never start, you fit continue dis session."
    stopped: "⚡ I don stop am. You fit continue dis session."
    no_active: "No work dey run wey I go stop."
    force_stopped_pending: "⚡ I don force stop am. The agent still dey start, the session don free."
  exec_approval:
    header: "Hermes wan run one command wey need your OK"
    reason_label: "Why e need OK"
    default_reason: "command wey fit cause wahala"
    timed_out_notice: "⌛ You no answer after {window}, so the command NO run. Tell me make I try again if you still want am, or make approvals.timeout long for config.yaml."
    action_once: "Allow am once"
    action_session: "Allow for dis session"
    action_always: "Always allow"
    action_deny: "No gree"
    deadline: "If you no answer inside {window}, e NO go run."
    not_requester: "Na only {requester} fit answer dis approval."
    not_requester_admins_only: "Na only admin or person wey dey the approver list fit answer dis approval."
    text_choice_once: "Reply `{prefix}approve` make e run one time"
    text_choice_session: "`{prefix}approve session` make dis kain command pass for the rest of dis session"
    text_choice_always: "`{prefix}approve always` make e always pass"
    text_choice_deny: "`{prefix}deny` to cancel am"
    text_choice_last: ", or {choice}.\n"
    truncated_full_above: "I shorten am small: the full command ({total} characters) dey the message wey dey up."
    truncated_deny_only: "I shorten am ({total} characters) and the full command no fit show for here, so na only Deny you fit press. Check am for the CLI or Desktop app."
    text_prompt_deny_only: "The approval message too long for dis platform, so na only Deny you fit press. Check am for the CLI or Desktop app."
  clarify:
    hint_single: "Reply with the number, the option, or your own answer."
    hint_multi: "You fit pick more than one: reply with the numbers, separate dem with comma or space (like \"1, 3\"), the option, or your own answer."
    question: "❓ {question}"
    answered: "✅ you answer: {response}"
    cancelled: "↩️ I don cancel the question. I go treat your message as new message."
    expired: "⏳ Dis question don expire. Abeg send new request."
    native_stream_placeholder: "💬 I dey wait make you choose..."
    skip_hint: 'Reply "skip" if you wan skip dis question.'
  unauthorized:
    action_notice: "Dis bot na private one and you no dey the allowed list. If na you get am, run `hermes pairing approve {platform} <request-id>` for the host (`hermes pairing list` go show the id)."
  errors:
    rate_limited: "⏱️ The AI model service dey slow down requests. Wait small, then use /retry."
    unreachable: "⚠️ The AI model service no dey reachable now. Wait small and use /retry; if e still dey happen, run `hermes doctor` for the host."
    usage_limit_resets: "⏱️ The AI model service limit don reach; e go reset in {window}. Use /retry after, or /model to change model."
```
