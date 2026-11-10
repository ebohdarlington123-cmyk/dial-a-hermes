"""A scripted OpenAI-compatible chat-completions server for the automated e2e test.

It is only a stand-in for a model so the approval path is deterministic:
- a user message containing "DELETE <path>" makes it call the real ``terminal`` tool with
  ``rm -rf <path>`` (a command Hermes flags as dangerous and sends for approval);
- after a tool result it answers with a summary that quotes the result;
- anything else gets a fixed long answer (to exercise USSD pagination + SMS).
Usage: python stub_llm.py <port>
"""
import json
import re
import sys
import time
import uuid

from aiohttp import web

LONG_ANSWER = ("Jos is the capital of Plateau State in central Nigeria. It sits on the Jos Plateau about "
               "1,200 metres above sea level, so it is cooler than most Nigerian cities. It is known for tin "
               "mining history, the Wildlife Park, Shere Hills and the National Museum. STUB-ANSWER-END")


def _last_user(messages):
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, list):
                c = " ".join(p.get("text", "") for p in c if isinstance(p, dict))
            return str(c or "")
    return ""


def _plan(body):
    msgs = body.get("messages") or []
    tail = msgs[-1] if msgs else {}
    if tail.get("role") == "tool":
        result = str(tail.get("content") or "")[:300]
        return {"content": f"Done. The terminal tool said: {result} STUB-TOOL-DONE"}
    user = _last_user(msgs)
    m = re.search(r"DELETE (\S+)", user)
    if m:
        return {"tool": {"name": "terminal", "arguments": json.dumps({"command": f"rm -rf {m.group(1)}"})}}
    return {"content": LONG_ANSWER}


def _completion(plan, model):
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]
    if "tool" in plan:
        msg = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_" + uuid.uuid4().hex[:8], "type": "function", "function": plan["tool"]}]}
        finish = "tool_calls"
    else:
        msg = {"role": "assistant", "content": plan["content"]}
        finish = "stop"
    return {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


async def chat(request):
    body = await request.json()
    plan = _plan(body)
    model = body.get("model", "stub")
    request.app["log"].append({"stream": bool(body.get("stream")), "plan": plan})
    if not body.get("stream"):
        return web.json_response(_completion(plan, model))
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]

    async def emit(delta, finish=None, usage=None):
        chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage:
            chunk["usage"] = usage
        await resp.write(f"data: {json.dumps(chunk)}\n\n".encode())

    await emit({"role": "assistant"})
    if "tool" in plan:
        await emit({"tool_calls": [{"index": 0, "id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                                    "function": {"name": plan["tool"]["name"], "arguments": plan["tool"]["arguments"]}}]})
        await emit({}, "tool_calls")
    else:
        text = plan["content"]
        for i in range(0, len(text), 40):
            await emit({"content": text[i:i + 40]})
        await emit({}, "stop")
    await emit({}, None, {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20})
    await resp.write(b"data: [DONE]\n\n")
    return resp


async def models(_request):
    return web.json_response({"object": "list", "data": [{"id": "stub-model", "object": "model",
                                                          "context_length": 131072}]})


async def calls(request):
    return web.json_response(request.app["log"])


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    app = web.Application()
    app["log"] = []
    app.router.add_post("/v1/chat/completions", chat)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/_calls", calls)
    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
