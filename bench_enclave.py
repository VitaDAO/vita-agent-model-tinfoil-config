#!/usr/bin/env python3
"""Measure TTFT, decode tok/s and MTP draft acceptance for the vita-agent-model
enclave. Stdlib only.

Point it at the local verified proxy:

    tinfoil container connect vita-agent-model -p 3301
    python3 bench_enclave.py

Usage:
  python3 bench_enclave.py [--base-url URL] [--model NAME] [--passes N]
                          [--max-tokens N] [--no-think]
"""

import argparse
import json
import statistics
import sys
import time
import urllib.request

# Author's recommended thinking-mode sampler. temperature must stay <= 1.0 and
# repetition_penalty at 1.0, or MTP draft acceptance collapses.
THINKING = {
    "temperature": 1.0,
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 0.0,
    "repetition_penalty": 1.0,
}
NON_THINKING = {
    "temperature": 0.7,
    "top_p": 0.80,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repetition_penalty": 1.0,
}

PROMPT = (
    "Write a detailed multi-paragraph essay on the history of container "
    "shipping and its effect on global trade. Do not use bullet points."
)


def run_pass(base_url, model, max_tokens, think):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
        "timings_per_token": True,
        **(THINKING if think else NON_THINKING),
    }
    if not think:
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    t_start = time.monotonic()
    t_first = None
    t_last = t_start
    chunks = 0
    usage = None
    timings = None
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            if event.get("usage"):
                usage = event["usage"]
            if event.get("timings"):
                timings = event["timings"]
            for choice in event.get("choices", []):
                delta = choice.get("delta") or {}
                # llama.cpp uses reasoning_content; SGLang streams `reasoning`.
                if (
                    delta.get("content")
                    or delta.get("reasoning")
                    or delta.get("reasoning_content")
                ):
                    t_last = time.monotonic()
                    if t_first is None:
                        t_first = t_last
                    chunks += 1
    if t_first is None:
        raise RuntimeError("no content received")
    out_tokens = (usage or {}).get("completion_tokens", chunks)
    decode_s = t_last - t_first
    return {
        "ttft": t_first - t_start,
        "tokens": out_tokens,
        "tok_s": out_tokens / decode_s if decode_s > 0 else float("inf"),
        "timings": timings,
    }


def draft_acceptance(base_url):
    """Read MTP draft acceptance off the Prometheus endpoint, if exposed."""
    try:
        root = base_url[:-3] if base_url.endswith("/v1") else base_url
        with urllib.request.urlopen(f"{root}/metrics", timeout=10) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    vals = {}
    for line in body.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        key, _, val = line.rpartition(" ")
        if "draft" in key or "spec" in key:
            try:
                vals[key.strip()] = float(val)
            except ValueError:
                pass
    return vals or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:3301/v1")
    ap.add_argument("--model", default="fable-711")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=800)
    ap.add_argument("--no-think", action="store_true")
    args = ap.parse_args()

    print(f"endpoint: {args.base_url}  model: {args.model}  "
          f"mode: {'non-thinking' if args.no_think else 'thinking'}")
    print(f"{'pass':>4} {'TTFT':>7} {'tokens':>7} {'tok/s':>8}")

    rates = []
    last_timings = None
    for i in range(args.passes):
        try:
            r = run_pass(args.base_url, args.model, args.max_tokens,
                         not args.no_think)
        except Exception as e:  # noqa: BLE001 - report and keep benching
            print(f"{i + 1:>4} ERROR: {e}", file=sys.stderr)
            continue
        rates.append(r["tok_s"])
        last_timings = r["timings"] or last_timings
        print(f"{i + 1:>4} {r['ttft']:>6.2f}s {r['tokens']:>7} "
              f"{r['tok_s']:>8.1f}")

    if rates:
        print(f"{'med':>4} {'':>7} {'':>7} {statistics.median(rates):>8.1f}")
    else:
        sys.exit("all passes failed")

    if last_timings:
        keep = {k: v for k, v in last_timings.items()
                if "draft" in k or "predicted_per_second" in k}
        if keep:
            print("\nserver timings:", json.dumps(keep, indent=2))

    acc = draft_acceptance(args.base_url)
    if acc:
        print("\ndraft counters (MTP):")
        for k, v in sorted(acc.items()):
            print(f"  {k} = {v:g}")
        print("  -> below ~50% acceptance, the non-MTP quants are faster.")


if __name__ == "__main__":
    main()
