#!/usr/bin/env python3
"""Standard protocol bench for the speed experiments. Stdlib only.
Usage: python3 bench_protocol.py <label>"""
import json, statistics, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "http://localhost:3301"
PROMPT = ("Write a detailed multi-paragraph essay on the history of container "
          "shipping and its effect on global trade. Do not use bullet points.")

def stream_run(sampler, max_tokens=800):
    payload = {"model": "fable-distill", "messages": [{"role": "user", "content": PROMPT}],
               "max_tokens": max_tokens, "stream": True,
               "stream_options": {"include_usage": True}, **sampler}
    req = urllib.request.Request(BASE + "/v1/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time(); first = None; toks = 0
    with urllib.request.urlopen(req, timeout=600) as r:
        for line in r:
            if line.startswith(b"data: ") and b"[DONE]" not in line:
                d = json.loads(line[6:])
                ch = d.get("choices")
                if first is None and ch and (ch[0].get("delta") or {}):
                    first = time.time()
                if d.get("usage"):
                    toks = d["usage"]["completion_tokens"]
    return toks, (first - t0), toks / (time.time() - first)

def accept_gauge():
    try:
        with urllib.request.urlopen(BASE + "/metrics", timeout=10) as r:
            for l in r.read().decode().splitlines():
                if "spec_accept_length" in l and not l.startswith("#"):
                    return float(l.split()[-1])
    except Exception:
        return None

def conc_one(i):
    payload = {"model": "fable-distill",
               "messages": [{"role": "user", "content": f"Explain topic #{i}: why the sky is blue, in detail."}],
               "max_tokens": 300, "temperature": 0.6, "top_p": 0.95,
               "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(BASE + "/v1/chat/completions",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)["usage"]["completion_tokens"]

label = sys.argv[1] if len(sys.argv) > 1 else "?"
stream_run({"temperature": 0.0}, 200)  # warm

g = [stream_run({"temperature": 0.0})[2] for _ in range(2)]
acc_g = accept_gauge()
t = [stream_run({"temperature": 1.0, "top_p": 0.95, "top_k": 20}) for _ in range(3)]
acc_t = accept_gauge()
tps = [x[2] for x in t]; ttfts = [x[1] for x in t]

t0 = time.time()
with ThreadPoolExecutor(8) as ex:
    toks = sum(ex.map(conc_one, range(8)))
agg = toks / (time.time() - t0)

print(f"[{label}] greedy: {statistics.median(g):.1f} tok/s (passes {['%.1f'%x for x in g]}) accept={acc_g}")
print(f"[{label}] t=1.0 : {statistics.median(tps):.1f} tok/s (passes {['%.1f'%x for x in tps]}) "
      f"TTFT {min(ttfts):.2f}-{max(ttfts):.2f}s accept={acc_t}")
print(f"[{label}] agg@8 : {agg:.1f} tok/s")
