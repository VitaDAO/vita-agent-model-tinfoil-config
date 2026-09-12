#!/usr/bin/env python3
"""Measure TTFT, decode tok/s and speculative draft acceptance for the vita-agent-model
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
    t_visible = None
    finish_reason = None
    done = False
    usage = None
    timings = None
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line == "data: [DONE]":
                done = True
                break
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("error"):
                raise RuntimeError("server returned an in-band streaming error")
            if event.get("usage"):
                usage = event["usage"]
            if event.get("timings"):
                timings = event["timings"]
            for choice in event.get("choices", []):
                if choice.get("finish_reason") is not None:
                    finish_reason = choice["finish_reason"]
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
                    if delta.get("content") and t_visible is None:
                        t_visible = t_last
    t_end = time.monotonic()
    if not done or finish_reason not in {"stop", "length"}:
        raise RuntimeError("stream did not finish with a valid terminal response")
    if t_first is None:
        raise RuntimeError("no content received")
    if finish_reason == "stop" and t_visible is None:
        raise RuntimeError("completed stream contains reasoning but no visible answer")
    out_tokens = (usage or {}).get("completion_tokens")
    if type(out_tokens) is not int or out_tokens <= 0:
        raise RuntimeError("stream did not report a positive completion token count")
    decode_s = t_last - t_first
    return {
        "ttft": t_first - t_start,
        "time_to_visible_answer": t_visible - t_start if t_visible is not None else None,
        "elapsed": t_end - t_start,
        "finish_reason": finish_reason,
        "tokens": out_tokens,
        # One SSE burst has no measurable decode interval; never report infinity.
        "tok_s": out_tokens / decode_s if decode_s > 0 else None,
        "end_to_end_tok_s": out_tokens / (t_end - t_start),
        "timings": timings,
    }


def draft_acceptance(base_url):
    """Read speculative draft acceptance off the Prometheus endpoint, if exposed."""
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
    ap.add_argument("--model", default="fable-distill")
    ap.add_argument("--passes", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=800)
    ap.add_argument("--no-think", action="store_true")
    args = ap.parse_args()
    if args.passes < 1 or args.max_tokens < 1:
        ap.error("passes and max-tokens must be positive")

    print(f"endpoint: {args.base_url}  model: {args.model}  "
          f"mode: {'non-thinking' if args.no_think else 'thinking'}")
    print(f"{'pass':>4} {'TTFT':>7} {'tokens':>7} {'tok/s':>8}")

    rates = []
    failed = 0
    last_timings = None
    for i in range(args.passes):
        try:
            r = run_pass(args.base_url, args.model, args.max_tokens,
                         not args.no_think)
        except Exception as e:  # noqa: BLE001 - report and keep benching
            print(f"{i + 1:>4} ERROR: {e}", file=sys.stderr)
            failed += 1
            continue
        if r["tok_s"] is None:
            print(f"{i + 1:>4} ERROR: only one content burst; decode rate unmeasurable", file=sys.stderr)
            failed += 1
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
        print("\nspeculative draft counters:")
        for k, v in sorted(acc.items()):
            print(f"  {k} = {v:g}")
        print("  Acceptance alone does not establish a speedup; compare matched runs.")
    if failed:
        sys.exit(f"{failed} benchmark passes failed; median covers successful passes only")


if __name__ == "__main__":
    main()
