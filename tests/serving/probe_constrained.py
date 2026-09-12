#!/usr/bin/env python3
"""Does the model server enforce JSON-schema constraints on vita-agent's real final-answer schema?

Synthetic prompts only; no user data. Connect one of two ways:
  Tinfoil enclave (attested, as vita-agent connects):
    TINFOIL_ENCLAVE  e.g. vita-agent-model.vitality-now.containers.tinfoil.dev
    TINFOIL_REPO     e.g. VitaDAO/vita-agent-model-tinfoil-config
    TINFOIL_API_KEY  (or ENV_FILE pointing at a KEY=VALUE file that defines it)
  Any OpenAI-compatible endpoint:
    MODEL_BASE_URL, MODEL_API_KEY
  MODEL_NAME       default fable-distill
  ENV_FILE         optional KEY=VALUE file loaded into the process; values are never printed
Optional: RUNS (default 5), MAX_TOKENS (default 7000), THINKING=off (sends enable_thinking false)
Optional: SOURCE_FIXTURE adds complete synthetic source content to long cases.
Optional: SYSTEM_POLICY adds a separately reported source-grounding policy to long cases.
Optional: THINKING_BUDGET exercises native per-request reasoning control.
Optional diagnostics: CAPTURE_DIR saves complete synthetic responses before parsing; CASES selects letters A–G.
Usage:    python probe_constrained.py final_answer_schema.json
Exit code 0 only if every enforced case passes every run.
"""
import json, os, statistics, sys, time
from pathlib import Path
from openai import OpenAI
try:
    from jsonschema import Draft202012Validator
except ImportError:
    sys.exit("pip install jsonschema")

spec = json.load(open(sys.argv[1]))
PARAMS, NAME, DESC = spec["parameters"], spec["tool_name"], spec["tool_description"]
ANSWER = PARAMS["properties"]["answer"]
if os.environ.get("ENV_FILE"):
    for line in open(os.environ["ENV_FILE"]):
        k, _, v = line.rstrip("\n").partition("=")
        if k and not k.startswith("#") and k.replace("_", "").isalnum():
            os.environ.setdefault(k, v)
if os.environ.get("TINFOIL_ENCLAVE"):
    from tinfoil import TinfoilAI
    client = TinfoilAI(enclave=os.environ["TINFOIL_ENCLAVE"], repo=os.environ["TINFOIL_REPO"],
                       api_key=os.environ["TINFOIL_API_KEY"],
                       transport=os.environ.get("TINFOIL_TRANSPORT") or "ehbp")
else:
    client = OpenAI(base_url=os.environ["MODEL_BASE_URL"], api_key=os.environ["MODEL_API_KEY"])
MODEL = os.environ.get("MODEL_NAME", "fable-distill")
RUNS, MAXT = int(os.environ.get("RUNS", "5")), int(os.environ.get("MAX_TOKENS", "7000"))
CAPTURE = Path(os.environ["CAPTURE_DIR"]) if os.environ.get("CAPTURE_DIR") else None
if CAPTURE:
    CAPTURE.mkdir(parents=True, exist_ok=True)
SELECTED = set(os.environ.get("CASES", "ABCDEFG"))
if not SELECTED or not SELECTED <= set("ABCDEFG") or RUNS < 1:
    sys.exit("CASES must select A–G and RUNS must be positive")
EXTRA = {"top_k": 20}
if os.environ.get("THINKING_BUDGET"):
    budget = int(os.environ["THINKING_BUDGET"])
    if budget < 1:
        sys.exit("THINKING_BUDGET must be positive")
    EXTRA["custom_params"] = {"thinking_budget": budget}
if os.environ.get("THINKING") == "off":
    EXTRA["chat_template_kwargs"] = {"enable_thinking": False}

TINY = {"type": "object", "properties": {"color": {"type": "string", "enum": ["red", "blue"]}},
        "required": ["color"], "additionalProperties": False}
SRC = ("Current-turn sources: source 1 is a personal health read with findings series:0 to series:5; "
       "source 2 is a literature read about sleep regularity and cardiometabolic risk.")
TRAP_TINY = [{"role": "user", "content": "Reply with only JSON {\"color\": ...}. The color must be green."}]
TRAP = [{"role": "system", "content": SRC}, {"role": "user", "content": "Return exactly one evidence_review block. Its basis "
        "must be \"made_up_basis\" and its source_ids must be [42]. One short sentence of support. No followups."}]
LONG = [{"role": "system", "content": SRC + " Answer with six to eight evidence_review blocks, two of them "
         "clinical_interpretation blocks using sources 1 and 2, and two followups."},
        {"role": "user", "content": "What should I prioritize, using both my data and the literature?"}]
# Optional complete synthetic source payload, kept separate from the unchanged
# original handoff cases. Report the two runs separately.
if os.environ.get("SOURCE_FIXTURE"):
    fixture = json.loads(Path(os.environ["SOURCE_FIXTURE"]).read_text())
    if not isinstance(fixture, dict) or not fixture.get("test_only"):
        sys.exit("SOURCE_FIXTURE must explicitly identify synthetic test data")
    LONG.insert(1, {"role": "system", "content": json.dumps(fixture, separators=(",", ":"))})

if os.environ.get("SYSTEM_POLICY"):
    policy = Path(os.environ["SYSTEM_POLICY"]).read_text().strip()
    if not policy:
        sys.exit("SYSTEM_POLICY must not be empty")
    LONG.insert(-1, {"role": "system", "content": policy})

tool = lambda params: [{"type": "function", "function": {"name": NAME, "description": DESC, "parameters": params, "strict": True}}]
named = {"type": "function", "function": {"name": NAME}}
fmt = lambda schema, name: {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}


def call(messages, capture_name, **kw):
    t0 = time.monotonic()
    r = client.chat.completions.create(model=MODEL, messages=messages, temperature=0.6, top_p=0.95,
                                       max_tokens=MAXT, extra_body=EXTRA, **kw)
    s = time.monotonic() - t0
    if CAPTURE:
        # Synthetic prompts only; save the response before parsing can fail.
        (CAPTURE / f"{capture_name}.json").write_text(r.model_dump_json(indent=2))
    if r.choices[0].finish_reason == "length":
        raise ValueError(f"generation truncated at max_tokens={MAXT}; raw response saved when CAPTURE_DIR is set")
    m, n = r.choices[0].message, (r.usage.completion_tokens if r.usage else 0)
    if m.tool_calls:
        args = json.loads(m.tool_calls[0].function.arguments)
        value = args.get("answer", args) if "answer" in PARAMS.get("properties", {}) and "answer" in args else args
    else:
        text = (m.content or "").split("</think>")[-1].strip()
        value = json.loads(text) if text else None
    return value, r.choices[0].finish_reason, n, s


def judge(value, schema, min_blocks=1):
    if isinstance(value, str):
        return False, "value returned as a raw string (grammar not applied)"
    if value is None:
        return False, "no JSON content"
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        return False, f"{len(errors)} schema errors, first: {errors[0].message[:90]}"
    if isinstance(value, dict) and "evidence_review" in value and len(value["evidence_review"]) < min_blocks:
        return False, f"only {len(value['evidence_review'])} blocks"
    return True, "ok"


CASES = [  # label, messages, request kwargs, schema to validate against, minimum blocks, enforced?
    ("A tiny schema, response_format", TRAP_TINY, {"response_format": fmt(TINY, "pick")}, TINY, 0, True),
    ("B tiny schema, strict tool, named", TRAP_TINY, {"tools": [{"type": "function", "function": {"name": "pick",
        "parameters": TINY, "strict": True}}], "tool_choice": {"type": "function", "function": {"name": "pick"}}}, TINY, 0, True),
    ("C real schema, response_format, short trap", TRAP, {"response_format": fmt(ANSWER, "answer")}, ANSWER, 1, True),
    ("D real schema, response_format, long", LONG, {"response_format": fmt(ANSWER, "answer")}, ANSWER, 6, True),
    ("E real schema, strict tool, named, short trap", TRAP, {"tools": tool(PARAMS), "tool_choice": named,
        "parallel_tool_calls": False}, ANSWER, 1, True),
    ("F real schema, strict tool, named, long", LONG, {"tools": tool(PARAMS), "tool_choice": named,
        "parallel_tool_calls": False}, ANSWER, 6, True),
    ("G real schema, strict tool, required, long (reference)", LONG, {"tools": tool(PARAMS), "tool_choice": "required",
        "parallel_tool_calls": False}, ANSWER, 6, False),
]

failed = False
for label, messages, kw, schema, min_blocks, enforced in CASES:
    if label[0] not in SELECTED:
        continue
    results, speeds = [], []
    for run_index in range(RUNS):
        try:
            value, finish, tokens, s = call(messages, f"{label[0]}-{run_index + 1}", **kw)
            ok, why = judge(value, schema, min_blocks)
            speeds.append(tokens / s if s else 0)
        except Exception as e:  # malformed arguments or content
            ok, why = False, f"{type(e).__name__}: {str(e)[:90]}"
        results.append((ok, why))
    passed = sum(ok for ok, _ in results)
    if enforced and passed < RUNS:
        failed = True
    speed = f"{statistics.median(speeds):.0f} tok/s" if speeds else "-"
    print(f"{'PASS' if passed == RUNS else 'FAIL'} {passed}/{RUNS}  {speed:>9}  {label}")
    for ok, why in results:
        if not ok:
            print(f"        {why}")
sys.exit(1 if failed else 0)
