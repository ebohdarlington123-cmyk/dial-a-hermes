# Dial-a-Hermes 📟

**Talk to your Hermes Agent from any phone, with no data, no app and no smartphone.** Dial a USSD
short code (like `*384*4242#`), type a question on the keypad, and read the answer in 182-character
screens. When Hermes wants to run something risky, **press 1 to approve or 2 to deny** from the same
keypad. If the answer is long, a full copy can arrive by SMS. Menus come in **English or Nigerian Pidgin**.

This repo holds two Hermes plugins:

| Plugin | Kind | What it does |
|---|---|---|
| [`plugins/dial-a-hermes`](https://github.com/ebohdarlington123-cmyk/dial-a-hermes/tree/main/plugins/dial-a-hermes) | `platform` (gateway adapter `ussd`) | Africa's Talking–compatible USSD callback, keypad approvals, optional SMS, feature-phone simulator |
| [`plugins/hermes-lang-pcm`](https://github.com/ebohdarlington123-cmyk/dial-a-hermes/tree/main/plugins/hermes-lang-pcm) | language pack (manifest-only) | Nigerian Pidgin (`pcm`) strings for approval prompts and gateway replies |

## Why

Hundreds of millions of people across Africa use feature phones, or smartphones with no data
bundle. USSD works on every GSM handset, needs no internet on the phone and costs little or nothing
for the caller. Dial-a-Hermes puts a full agent (tools, memory, skills, approvals) behind a short
code. The owner can check on a long-running task, answer an approval or ask a question from a 3310
while their laptop is elsewhere.

## How it works

```
phone ──USSD──▶ telco ──▶ Africa's Talking ──HTTPS POST──▶ tunnel ──▶ Hermes gateway (ussd adapter)
   ◀── CON/END screen (≤182 chars, answered in <10 s) ◀─────────────────────────┘
   ◀── optional SMS with the full reply (AT SMS API) ◀── adapter
```

* Africa's Talking (AT) sends `sessionId, phoneNumber, serviceCode, text`. `text` is the whole keypad
  history joined with `*`. The adapter replies with `CON <screen>` (keep the session open) or
  `END <screen>` (hang up).
* AT gives each callback about **10 s**, so the adapter waits up to `USSD_REPLY_WAIT_SECONDS` (default
  6). If Hermes isn't done, the phone shows `Hermes is thinking... 1 Check again`. The agent turn keeps
  running in the background. You can also hang up and dial back later (main menu → `2 Last reply`).
* Long answers are paged: `(1/3) … 1 More 0 Back 9 Menu`. `7 SMS` sends the full text.
* Exec approvals are Hermes's own approval cards (`_send_exec_approval_prompt`). They are answered
  through `tools.approval.resolve_gateway_approval_card`, so Hermes's approval policy, timeout and
  audit trail apply unchanged. The phone shows the **whole command**, paged with `3 More`, and
  `1 Approve` is only offered once you reach the last page. Only *once* and *deny* are offered, never
  "always", on a keypad.

## Quick demo (local, no accounts)

```bash
hermes plugins install ebohdarlington123-cmyk/dial-a-hermes   # or copy plugins/dial-a-hermes to ~/.hermes/plugins/
hermes plugins enable dial-a-hermes
TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
printf 'USSD_ALLOWED_USERS=+2348031234567\nUSSD_CALLBACK_TOKEN=%s\nUSSD_SIMULATOR=true\nUSSD_SMS_MODE=simulator\n' "$TOKEN" >> ~/.hermes/.env
echo "simulator: http://127.0.0.1:8095/simulator/$TOKEN"
hermes gateway run
```
Or run `scripts/run_demo.sh`, which does all of this in a throwaway `HERMES_HOME`.

Open **http://127.0.0.1:8095/simulator/&lt;USSD_CALLBACK_TOKEN&gt;**: a feature-phone keypad that posts
to the same callback AT would use.

| Do this | See this |
|---|---|
| Press **Send** with an empty input (that "dials" `*384*4242#`) | `Dial-a-Hermes / 1 Ask Hermes / 2 Last reply / 3 Approvals (0) / 4 Language / 0 Exit` |
| Type `1`, Send, then type a question, Send | The answer, paged `(1/n)`; or `Hermes is thinking... 1 Check again` if it takes longer than 6 s |
| Press `1` on an answer page | The next page. On a long answer an SMS toast appears ("full reply") |
| Ask "delete the folder /tmp/demo-folder" | `Hermes wants to run (1/1): <the delete command> / Why: … / 1 Approve 2 Deny` |
| Press `1` | `Approved. Hermes is continuing.` and the folder is gone |
| Press `2` instead | `Denied. Hermes will not run it.` and the folder stays |
| From the main menu: `4`, then `2` | Menus switch to Pidgin: `0 Comot`, `1 I gree 2 I no gree` |
| Change the phone number in the simulator to one not in the allowlist | `END Sorry, this number is not allowed to use this Hermes.` |

## Real phone: Africa's Talking sandbox + tunnel

1. Create a free account at <https://account.africastalking.com> and open the **Sandbox** app.
2. Expose the local listener with a tunnel, for example `cloudflared tunnel --url http://127.0.0.1:8095`
   or `ngrok http 8095`.
3. In the sandbox go to **USSD → Create Channel**, pick a code (e.g. `*384*4242#`) and set the callback
   URL to `https://<your-tunnel-host>/ussd/<USSD_CALLBACK_TOKEN>`.
4. Optional SMS: under **Settings → API Key** generate a sandbox key and set `USSD_SMS_MODE=africastalking`,
   `AT_USERNAME=sandbox` and `AT_API_KEY=<key>` (via `hermes config` or `~/.hermes/.env`, never in the repo).
5. Open the AT simulator (<https://simulator.africastalking.com:1517>), enter your allowlisted
   number, and dial your code. SMS replies show up in the same simulator.
6. Going live (real telcos) needs a live AT account and a USSD code from AT; set `AT_USERNAME` to your
   live app username.

## Configuration

All settings come from Hermes's normal config: `~/.hermes/.env`, or `platforms.ussd.extra` in
`config.yaml`. Secrets are read with Hermes's scoped-secret helpers.

| Variable | Default | Meaning |
|---|---|---|
| `USSD_ALLOWED_USERS` | **required** | Comma-separated phone numbers allowed to use the agent (the owner). Empty means nobody; the adapter fails closed. `0803…` and `+234803…` both match. |
| `USSD_CALLBACK_TOKEN` | **required** | Secret path segment (≥16 chars) for the callback URL: `/ussd/<token>` |
| `USSD_HOST` / `USSD_PORT` | `127.0.0.1` / `8095` | Local listener (put a tunnel or reverse proxy in front) |
| `USSD_DEFAULT_COUNTRY_CODE` | `234` | Used to normalise local numbers (`0803…` → `+234803…`) |
| `USSD_LANGUAGE` | `en` | Default menu language (`en` or `pcm`); each phone can switch from menu `4` |
| `USSD_REPLY_WAIT_SECONDS` | `6` | How long a callback waits for the agent (0–9; AT's limit is about 10 s) |
| `USSD_SCREEN_CHARS` | `182` | Max characters per screen |
| `USSD_SMS_MODE` | `off` | `off`, `africastalking` (AT SMS API) or `simulator` (local inbox for the simulator) |
| `USSD_SMS_REPLIES` | `long` | `long` = SMS when the reply needs more than one screen, `always`, or `off` (manual `7 SMS` only) |
| `AT_USERNAME` | `sandbox` | Africa's Talking app username (`sandbox` uses the sandbox API host) |
| `AT_API_KEY` | none | Africa's Talking API key (secret), needed only for `USSD_SMS_MODE=africastalking` |
| `AT_SENDER_ID` | none | Optional alphanumeric sender ID / short code for SMS |
| `USSD_SIMULATOR` | `false` | Serve the feature-phone simulator. Only allowed when `USSD_HOST` is a loopback address. |
| `USSD_HOME_CHANNEL` | first allowed number | Where cron and notification messages go |

Pidgin for the agent's own messages (approval prompts, "command denied", etc.): install
`plugins/hermes-lang-pcm` and set `hermes config set display.language pcm`.

## Safety notes

* **Owner-only.** Only numbers in `USSD_ALLOWED_USERS` can use the agent. Every other caller gets
  `END Sorry, this number is not allowed…`, and the log records only a redacted number. There is no
  pairing flow over USSD, and an empty allowlist means nobody.
* **The callback token is the security boundary.** Africa's Talking does not sign USSD callbacks, and
  caller-ID (`phoneNumber`) is only as trustworthy as the request. Anyone who knows the full callback
  URL could post a fake `phoneNumber`. Keep `USSD_CALLBACK_TOKEN` long and random, only share it with
  AT, and rotate it if leaked. The token is compared in constant time; wrong tokens get a plain 404. For
  extra hardening, allowlist AT's egress IPs at your proxy.
* **Approvals are Hermes's.** The adapter never auto-approves and never offers "always". It shows the
  full command before `1 Approve` appears. A truncated or Deny-only card stays Deny-only. Timeouts and
  `approvals.unattended_mode` (default deny) are Hermes's own, so a request nobody answers is denied
  and nothing runs.
* **Unattended / failure behaviour.** If the listener can't start, or config is missing or invalid,
  the adapter reports a clean, non-retryable fatal error and the rest of the gateway keeps running. It
  never crashes the gateway. Requests are capped at 16 KiB.
* **Simulator is local-only.** It refuses to start unless the listener is bound to loopback.
* **Phone numbers are personal data.** Logs redact them (`+***4567`). Language preferences live in
  the plugin's data dir (`plugin_data_dir("dial-a-hermes")/language.json`).

## Network calls (disclosure)

* **Inbound:** one local HTTP listener (`USSD_HOST:USSD_PORT`), serving `POST /ussd/<token>`,
  `GET /health`, and (only when `USSD_SIMULATOR=true`) `GET /simulator/<token>`.
* **Outbound:** only when `USSD_SMS_MODE=africastalking`. It POSTs to
  `https://api.sandbox.africastalking.com/version1/messaging` (sandbox) or
  `https://api.africastalking.com/version1/messaging` (live) with the reply text and the owner's
  number. There is no other outbound traffic, no telemetry and no downloads.
* The text you type goes to whatever model provider your Hermes is configured with, as on every
  platform.

## Tests

```bash
pip install pytest && pytest tests/test_core.py            # 34 unit tests: paging, sessions, approvals, allowlist, AT format
python tests/e2e/run_e2e.py --provider stub                 # real `hermes gateway run` + plugin, scripted model
python tests/e2e/run_e2e.py --provider gemini --model gemini-3.8-flash   # same flows against a real model (GEMINI_API_KEY)
hermes plugins validate plugins/dial-a-hermes --install-deps
hermes plugins validate plugins/hermes-lang-pcm
```

## Licence

MIT © 2026 Ebube Eboh
