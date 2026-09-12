"""Matched synthetic accuracy/latency slices; never substitutes for app evaluation.

MODEL_BASE_URL must name the isolated test endpoint. Saves synthetic outputs for
human review; exact-answer checks deliberately do not constrain the correct answer.
Usage: python benchmark_profiles.py --output /path/to/results [--repeats 2]
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import statistics
import time

from openai import OpenAI

CASES = [
    ("arithmetic", "Calculate 42 times 17. Return only the integer.", "714"),
    ("recall", "Synthetic saved notes: Sunday Windows is a reminder to open windows on Sunday at 09:00. Birch Tide is a reminder to water a birch on Tuesday at 18:00. What time is Sunday Windows? Return only HH:MM.", "09:00"),
    ("individual_change", "Synthetic evidence: one person's HRV changed from 42 to 47 ms. A device validation study reports MAE 4 ms across 60 adults. No personal repeated readings, repeatability, or minimal detectable change is available. Can this evidence establish whether that individual's change exceeds measurement noise? Return only YES or NO.", "NO"),
    ("cutoff", "Synthetic paper: participants with ApoB >=130 mg/dL were placed in the high-exposure study group. It reports no treatment recommendations or individual target. Does this study establish a treatment target of 130 mg/dL for the user? Return only YES or NO.", "NO"),
    ("grounded_summary", "Use only this synthetic evidence. Personal data: mean sleep 360 min/night across 14 nights, bedtime standard deviation 95 min, resting heart rate 62 bpm, 6000 steps/day. No historical baseline or diagnosis. A synthetic observational study of 60 adults over 4 weeks associated more variable sleep timing with less favorable cardiometabolic markers. It did not test interventions or establish causation. In 100-160 words, explain what the data shows, what remains unknown, and a proportionate next step. Do not invent clinical targets or claim a proven individual benefit.", None),
]


def run_case(base, case, budget, schema=None):
    name, prompt, expected = case
    extra = {"top_k": 20}
    if budget is not None:
        extra["custom_params"] = {"thinking_budget": budget}
    start = time.monotonic()
    first = visible = None
    content = []
    usage = None
    finish = None
    with OpenAI(base_url=base, api_key=os.environ.get("MODEL_API_KEY", "local-test"), timeout=180, max_retries=0) as client:
        stream = client.chat.completions.create(
            model="fable-distill", messages=[{"role": "user", "content": prompt}],
            temperature=0.6, top_p=0.95, max_tokens=6500, extra_body=extra,
            stream=True, stream_options={"include_usage": True},
            **({"response_format": {"type": "json_schema", "json_schema": {"name": "fixture", "strict": True, "schema": schema}}} if schema is not None else {}),
        )
        for chunk in stream:
            if getattr(chunk, "error", None):
                raise RuntimeError("in-band stream error")
            if chunk.usage:
                usage = chunk.usage.model_dump()
            for choice in chunk.choices:
                delta = choice.delta
                now = time.monotonic() - start
                if delta.content or getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None):
                    if first is None:
                        first = now
                if delta.content:
                    if visible is None:
                        visible = now
                    content.append(delta.content)
                if choice.finish_reason:
                    finish = choice.finish_reason
    answer = "".join(content).strip()
    if finish != "stop" or not answer or not usage or not usage.get("completion_tokens"):
        raise RuntimeError(f"Incomplete response: finish={finish}, usage={usage}")
    return {"case": name, "thinking_budget": budget, "ttft": first,
            "visible_seconds": visible, "elapsed": time.monotonic() - start,
            "finish": finish, "usage": usage, "answer": answer,
            "exact_answer_pass": answer == expected if expected is not None else None,
            "budget_enforced": (type(usage.get("reasoning_tokens")) is int and 0 <= usage["reasoning_tokens"] <= budget + 2) if budget is not None else None,
            "word_count": len(answer.split())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    if args.repeats < 1 or args.concurrency < 1:
        parser.error("repeats and concurrency must be positive")
    base = os.environ["MODEL_BASE_URL"]
    args.output.mkdir(parents=True, exist_ok=True)
    # Rotate order across cases/repeats. Four repeats balance each budget across
    # every order position for a case; do not credit a later warm run to its budget.
    budgets = (None, 32, 128, 512)
    jobs = []
    for repeat in range(args.repeats):
        for index, case in enumerate(CASES):
            offset = (repeat + index) % len(budgets)
            for budget in budgets[offset:] + budgets[:offset]:
                jobs.append((repeat, case, budget))
    def run(job):
        repeat, case, budget = job
        try:
            result = run_case(base, case, budget)
        except Exception as exc:
            result = {"case": case[0], "thinking_budget": budget, "error": str(exc)}
        result["repeat"] = repeat
        (args.output / f'{case[0]}-{budget}-{repeat}.json').write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k != "answer"}), flush=True)
        return result
    batch_start = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(run, jobs))
    batch_elapsed = time.monotonic() - batch_start
    failed = [r for r in results if "error" in r or r.get("exact_answer_pass") is False or r.get("budget_enforced") is False]
    summary = {"requests": len(results), "failed": len(failed), "manual_review_required": "grounded_summary outputs",
               "concurrency": args.concurrency, "batch_elapsed": batch_elapsed,
               "aggregate_completion_tokens_per_second": sum(r.get("usage", {}).get("completion_tokens", 0) for r in results) / batch_elapsed, "budgets": {}}
    for budget in (None, 32, 128, 512):
        rows = [r for r in results if r["thinking_budget"] == budget and "error" not in r]
        if rows:
            summary["budgets"][str(budget)] = {"mean_elapsed": statistics.mean(r["elapsed"] for r in rows),
                "mean_visible_seconds": statistics.mean(r["visible_seconds"] for r in rows),
                "exact_failures": sum(r["exact_answer_pass"] is False for r in rows)}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary), flush=True)
    return bool(failed)

if __name__ == "__main__":
    raise SystemExit(main())
