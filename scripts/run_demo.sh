#!/usr/bin/env bash
# Start a throwaway Hermes with Dial-a-Hermes + the feature-phone simulator for a local demo.
# Uses your normal model provider settings if MODEL_PROVIDER/MODEL_NAME are given, e.g.
#   MODEL_PROVIDER=gemini MODEL_NAME=gemini-3.8-flash scripts/run_demo.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HERMES_HOME="${HERMES_HOME:-$(mktemp -d -t dah-demo-XXXX)}"
PHONE="${DEMO_PHONE:-+2348031234567}"
TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')"
mkdir -p "$HERMES_HOME/plugins"
cp -r "$ROOT/plugins/dial-a-hermes" "$ROOT/plugins/hermes-lang-pcm" "$HERMES_HOME/plugins/"
hermes plugins enable dial-a-hermes </dev/null
hermes plugins enable hermes-lang-pcm </dev/null
if [[ -n "${MODEL_PROVIDER:-}" ]]; then
  hermes config set model.provider "$MODEL_PROVIDER"
  hermes config set model.default "${MODEL_NAME:?set MODEL_NAME too}"
fi
hermes config set approvals.mode manual   # every risky command asks the phone, which is good for the demo
cat >> "$HERMES_HOME/.env" <<ENV
USSD_ALLOWED_USERS=$PHONE
USSD_CALLBACK_TOKEN=$TOKEN
USSD_SIMULATOR=true
USSD_SMS_MODE=simulator
ENV
echo
echo "  HERMES_HOME : $HERMES_HOME"
echo "  Simulator   : http://127.0.0.1:${USSD_PORT:-8095}/simulator/$TOKEN"
echo "  Phone       : $PHONE   (short code *384*4242#)"
echo
exec hermes gateway run
