# hermes-lang-pcm: Nigerian Pidgin (Naijá) for Hermes Agent

A partial, manifest-only language pack. It translates Hermes's approval prompts ("DIS COMMAND FIT
CAUSE WAHALA", "I no gree"), the approve/deny/stop replies, clarify prompts and a few gateway
notices. It also adds Pidgin words to the approval inputs (`gree`, `i gree`, `oya`, `no gree`,
`leave am`) next to the English ones. Every other key falls back to English.

```bash
hermes plugins install ebohdarlington123-cmyk/dial-a-hermes/plugins/hermes-lang-pcm
hermes plugins enable hermes-lang-pcm
hermes config set display.language pcm
```

No code, no network, no secrets. Placeholders (`{count}`), `/commands`, `code` and config keys are
unchanged from English. It pairs with [Dial-a-Hermes](https://github.com/ebohdarlington123-cmyk/dial-a-hermes)
(USSD for feature phones), which has its own Pidgin keypad menus. MIT licence.
