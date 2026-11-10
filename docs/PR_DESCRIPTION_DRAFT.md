<!-- DRAFT: do not open until the repo is public and the sha is filled in. One PR per entry file. -->
## Add dial-a-hermes to the plugin catalog

Adds `plugin-catalog/dial-a-hermes.yaml`, pinned to `ee1117f21afd63e473d9605e4a26a435ffb7a628` of
https://github.com/ebohdarlington123-cmyk/dial-a-hermes (subdir `plugins/dial-a-hermes`). I own and maintain the repo.

**What it does:** lets you talk to your Hermes from any phone over USSD, with no data, app or smartphone.
You dial a short code, type a question on the keypad and read the answer in 182-char pages. Exec
approvals are answered with 1 Approve / 2 Deny, and the full command is shown before Approve is
offered. A full reply can also arrive by SMS. Menus are in English or Nigerian Pidgin. The callback
format is compatible with Africa's Talking, and a local feature-phone simulator is included.

**Surfaces used:** `kind: platform` → `ctx.register_platform("ussd", …)` with a `BasePlatformAdapter`
subclass (`connect/disconnect/send/get_chat_info`, `on_processing_complete`, `_send_exec_approval_prompt`,
`send_clarify`). Approvals resolve through `tools.approval.resolve_gateway_approval_card`, clarify through
`tools.clarify_gateway.resolve_gateway_clarify`, secrets through `gateway.platforms._shared.extra_or_secret` /
`get_scoped_secret`, and storage through `plugin_data_dir`. No tools, hooks or middleware. It does not
patch or override core.

**Disclosure (rule 13):**
- Opens a local HTTP listener (default `127.0.0.1:8095`) for `POST /ussd/<secret-token>` and `GET /health`.
  A simulator page is served only when `USSD_SIMULATOR=true`, and it refuses to start unless bound to loopback.
- Africa's Talking does not sign callbacks, so the secret token in the URL is the security boundary.
  The token is compared in constant time, wrong tokens get a 404, and request bodies are capped at 16 KiB.
- Only phones in `USSD_ALLOWED_USERS` can use the agent. An empty list means nobody, and there is no
  pairing over USSD.
- Outbound network only when `USSD_SMS_MODE=africastalking`: a POST to `api.africastalking.com` /
  `api.sandbox.africastalking.com` with the reply text and the owner's number.
- Stores each phone's language choice in the plugin's data folder. No telemetry.
- Approvals: the adapter never auto-approves and never offers "always". Deny-only cards stay Deny-only, and
  timeouts and unattended mode are Hermes's own, so an unanswered request is denied and nothing runs.

**Checked:** `hermes plugins validate --install-deps` passes on v0.23.1, and
`scripts/validate_plugin_catalog.py` passes. There are 34 unit tests, plus an e2e run against a real
`hermes gateway run` (question → paged answer + SMS; approve → command ran; deny → it didn't;
non-allowlisted number refused; bad token → 404).
Demo video: <X post link>.
