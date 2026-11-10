"""End-to-end test: a real Hermes gateway + the Dial-a-Hermes plugin + USSD over HTTP.

    python tests/e2e/run_e2e.py --provider stub      # deterministic, no keys (scripted model)
    python tests/e2e/run_e2e.py --provider gemini    # real model; needs GEMINI_API_KEY in the environment

It creates a throwaway HERMES_HOME, installs and enables the plugin from this checkout, starts
`hermes gateway run`, then plays Africa's Talking-style USSD sessions against the callback:
a question (paged reply + SMS to the simulator inbox), an approval answered with 1 (the command
really runs), an approval answered with 2 (it does not), Pidgin menus, a refused number and a
wrong token. Exit code 0 means every check passed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "dial-a-hermes"
PHONE = "+2348031234567"
TOKEN = "e2e-" + os.urandom(8).hex()
PORT = int(os.environ.get("DAH_E2E_PORT", "8096"))
STUB_PORT = int(os.environ.get("DAH_E2E_STUB_PORT", "8766"))
HERMES = os.environ.get("HERMES_BIN") or shutil.which("hermes") or "hermes"
CHECKS: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok))
    print(("  PASS " if ok else "  FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def post(session: str, text: str, phone: str = PHONE, token: str = TOKEN) -> str:
    data = urllib.parse.urlencode({"sessionId": session, "phoneNumber": phone, "serviceCode": "*384*4242#",
                                   "networkCode": "62130", "text": text}).encode()
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PORT}/ussd/{token}", data=data),
                                    timeout=15) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        body = f"HTTP {e.code}"
    dt = time.monotonic() - t0
    print(f"    > text={text!r} ({dt:.1f}s)\n      " + body.replace("\n", "\n      "))
    check(f"responded inside the 10s USSD window ({dt:.1f}s)", dt < 10)
    return body


def sms_inbox() -> list[str]:
    url = f"http://127.0.0.1:{PORT}/simulator/{TOKEN}/sms?phone=" + urllib.parse.quote(PHONE)
    with urllib.request.urlopen(url, timeout=5) as r:
        return [m["text"] for m in json.loads(r.read())["messages"]]


class Session:
    def __init__(self, sid: str):
        self.sid, self.keys = sid, []

    def press(self, key: str | None = None) -> str:
        if key is not None:
            self.keys.append(key)
        return post(self.sid, "*".join(self.keys))

    def until(self, pred, key: str = "1", tries: int = 20) -> str:
        """Press `key` ("check again") until pred(screen) or give up."""
        out = ""
        for _ in range(tries):
            out = self.press(key)
            if pred(out):
                return out
            time.sleep(2)
        return out


def read_all_pages(ses: Session, first: str) -> str:
    pages, out = [first], first
    while "1 More" in out:
        out = ses.press("1")
        pages.append(out)
    return "\n".join(pages)


def run(cmd, env, **kw):
    return subprocess.run(cmd, env=env, capture_output=True, text=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["stub", "gemini"], default="stub")
    ap.add_argument("--model", default="gemini-3.6-flash")
    ap.add_argument("--keep", action="store_true", help="keep the temp HERMES_HOME")
    args = ap.parse_args()
    if args.provider == "gemini" and not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set; use --provider stub")
        return 2

    home = Path(tempfile.mkdtemp(prefix="dah-e2e-"))
    env = {**os.environ, "HERMES_HOME": str(home)}
    procs: list[subprocess.Popen] = []
    print(f"HERMES_HOME={home}\nhermes: {run([HERMES, '--version'], env).stdout.splitlines()[0]}")
    try:
        (home / "plugins").mkdir(parents=True)
        shutil.copytree(PLUGIN, home / "plugins" / "dial-a-hermes",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for attempt in range(3):  # PM downloads can flake on some networks; retry
            r = run([HERMES, "plugins", "enable", "dial-a-hermes", "--no-allow-tool-override"], env, input="y\n",
                    timeout=900)
            if r.returncode == 0 and "enabled" in r.stdout:
                break
            run([HERMES, "pm", "doctor"], env, timeout=600)
        check("plugin enabled in a fresh HERMES_HOME", "enabled" in r.stdout, r.stdout[-300:] + r.stderr[-300:])

        model = (f"model:\n  provider: custom\n  base_url: http://127.0.0.1:{STUB_PORT}/v1\n  default: stub-model\n"
                 f"  api_key: stub-not-a-secret\n" if args.provider == "stub"
                 else f"model:\n  provider: gemini\n  default: {args.model}\n")
        with open(home / "config.yaml", "a") as f:
            f.write(model + "approvals:\n  mode: manual\n  timeout: 300\n"
                    "terminal:\n  backend: local\n  cwd: /tmp\n")
        (home / ".env").write_text(
            f"USSD_ALLOWED_USERS={PHONE}\nUSSD_CALLBACK_TOKEN={TOKEN}\nUSSD_SIMULATOR=true\n"
            f"USSD_SMS_MODE=simulator\nUSSD_PORT={PORT}\n")
        if args.provider == "stub":
            procs.append(subprocess.Popen([sys.executable, str(ROOT / "tests" / "e2e" / "stub_llm.py"), str(STUB_PORT)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        gw_log = open(home / "gateway.out", "w")
        procs.append(subprocess.Popen([HERMES, "gateway", "run"], env=env, cwd="/tmp", stdout=gw_log,
                                      stderr=subprocess.STDOUT, start_new_session=True))
        for _ in range(90):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2)
                break
            except Exception:
                time.sleep(1)
        else:
            check("gateway started the USSD listener", False, (home / "gateway.out").read_text()[-800:])
            return 1
        check("gateway started the USSD listener", True)

        print("\n[1] ask a question, read the paged answer, SMS copy")
        s = Session("e2e-ask")
        out = s.press()
        check("dial shows the main menu", out.startswith("CON Dial-a-Hermes") and "1 Ask Hermes" in out)
        check("ask screen", "Type your question" in s.press("1"))
        q = ("Tell me about Jos" if args.provider == "stub"
             else "In two short sentences: what is Jos in Nigeria known for?")
        out = s.press(q)
        if "thinking" in out:
            check("slow reply shows 'Thinking... press 1'", "1 Check again" in out)
            out = s.until(lambda o: "thinking" not in o)
        check("answer arrives on the phone", out.startswith("CON (1/") or out.startswith("CON " ) and
              "thinking" not in out and "No reply" not in out, out)
        full = read_all_pages(s, out)
        check("no gateway home-channel nag", "home channel" not in full.lower())
        if args.provider == "stub":
            check("whole answer readable across pages", "STUB-ANSWER-END" in full)
        else:
            check("real model answer mentions Jos/Plateau", any(w in full for w in ("Plateau", "Jos", "tin")))
        screens = [line for line in full.split("\n") if line.startswith("CON ")]
        check("every screen <= 182 chars", all(len(p) <= 182 for p in full.split("\nCON ")))
        time.sleep(1)
        inbox = sms_inbox()
        if len([p for p in screens]) > 1:
            check("long reply copied to SMS inbox", len(inbox) >= 1)

        print("\n[2] approval: press 1 -> the command really runs")
        target = Path(tempfile.mkdtemp(prefix="dah-approve-", dir="/tmp"))
        (target / "file.txt").write_text("x")
        s = Session("e2e-approve")
        s.press(), s.press("1")
        ask = (f"DELETE {target}" if args.provider == "stub"
               else f"Use the terminal tool to run exactly this command and nothing else: rm -rf {target}")
        out = s.press(ask)
        out = out if "wants to run" in out else s.until(lambda o: "wants to run" in o or "thinking" not in o)
        check("approval prompt reaches the keypad", "wants to run" in out and "1 Approve 2 Deny" in out, out)
        check("prompt shows the real command", str(target) in out)
        check("directory still exists before approval", target.exists())
        out = s.press("1")
        out = out if "thinking" not in out else s.until(lambda o: "thinking" not in o)
        time.sleep(1)
        check("approved command ran (directory deleted)", not target.exists())

        print("\n[3] approval: press 2 -> it does not run; menus in Pidgin")
        keep = Path(tempfile.mkdtemp(prefix="dah-deny-", dir="/tmp"))
        (keep / "keep.txt").write_text("x")
        s = Session("e2e-deny")
        s.press()
        s.press("4")
        out = s.press("2")
        check("Pidgin menu", "Comot" in out and "Language don change to Pidgin" in out)
        s.press("1")
        ask = (f"DELETE {keep}" if args.provider == "stub"
               else f"Use the terminal tool to run exactly this command and nothing else: rm -rf {keep}")
        out = s.press(ask)
        out = out if "wan run" in out else s.until(lambda o: "wan run" in o or "dey think" not in o)
        check("Pidgin approval prompt", "Hermes wan run dis" in out and "1 I gree 2 I no gree" in out, out)
        out = s.press("2")
        out = out if "dey think" not in out else s.until(lambda o: "dey think" not in o)
        time.sleep(1)
        check("denied command did not run (directory kept)", (keep / "keep.txt").exists())
        shutil.rmtree(keep, ignore_errors=True)

        print("\n[4] safety")
        out = post("e2e-stranger", "", phone="+2348000000000")
        check("non-allowlisted number refused", out.startswith("END ") and "not allowed" in out)
        out = post("e2e-badtoken", "", token="wrong-token-wrong-token")
        check("wrong callback token gets 404", out == "HTTP 404")
    finally:
        for p in procs:
            try:
                os.killpg(p.pid, signal.SIGTERM) if p.args[0] == HERMES else p.terminate()
            except Exception:
                pass
        for p in procs:
            try:
                p.wait(timeout=20)
            except Exception:
                p.kill()
        if not args.keep:
            shutil.rmtree(home, ignore_errors=True)
    failed = [n for n, ok in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
