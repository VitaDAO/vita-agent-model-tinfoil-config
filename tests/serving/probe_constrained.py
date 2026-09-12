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
Optional diagnostics: CAPTURE_DIR saves each complete synthetic request (credential
fields redacted) and response before parsing; CASES selects letters A–G.
Long D/F/G report the original minimum-block metric and the stricter prompt-obligation
audit (blocks, clinical_interpretation composition, followups) separately.
Usage:    python probe_constrained.py final_answer_schema.json
Exit code 0 only if every enforced case passes every run under the strict audit.
"""
import hashlib, json, os, statistics, sys, time, urllib.parse
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
    client = OpenAI(base_url=os.environ["MODEL_BASE_URL"], api_key=os.environ["MODEL_API_KEY"],
                    timeout=150, max_retries=0)
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


def safe_base_url(url):
    """Endpoint URL without userinfo, query or fragment, which can carry credentials."""
    if not url:
        return None
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None


SECRET_FIELD_NAMES = {"api_key", "apikey", "authorization", "auth", "token", "access_token",
                      "refresh_token", "client_secret", "secret", "password", "bearer"}


def redact(payload):
    """Replace credential-shaped fields before any request reaches disk.

    Names are matched whole, so ordinary request settings such as max_tokens
    survive. The client keeps the API key outside the request body; this is a
    guard against it ever being added there.
    """
    if isinstance(payload, dict):
        return {key: ("<redacted>" if str(key).strip().lower().replace("-", "_") in SECRET_FIELD_NAMES
                      else redact(value)) for key, value in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact(item) for item in payload]
    return payload


PROBE_SETTINGS = {
    "model": MODEL,
    "runs": RUNS,
    "max_tokens": MAXT,
    "temperature": 0.6,
    "top_p": 0.95,
    "extra_body": EXTRA,
    "cases": sorted(SELECTED),
    "tinfoil_enclave": os.environ.get("TINFOIL_ENCLAVE") or None,
    "tinfoil_repo": os.environ.get("TINFOIL_REPO") or None,
    "model_base_url": None if os.environ.get("TINFOIL_ENCLAVE") else safe_base_url(os.environ.get("MODEL_BASE_URL")),
    "source_fixture": {"path": os.environ.get("SOURCE_FIXTURE") or None,
                       "sha256": digest_file(os.environ.get("SOURCE_FIXTURE"))},
    "system_policy": {"path": os.environ.get("SYSTEM_POLICY") or None,
                      "sha256": digest_file(os.environ.get("SYSTEM_POLICY"))},
}

tool = lambda params: [{"type": "function", "function": {"name": NAME, "description": DESC, "parameters": params, "strict": True}}]
named = {"type": "function", "function": {"name": NAME}}
fmt = lambda schema, name: {"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}}


def parse_tool_arguments(m, tools, tool_choice):
    choice = tool_choice
    if isinstance(choice, dict):
        expected_name = choice["function"]["name"]
        if len(m.tool_calls or []) != 1 or m.tool_calls[0].function.name != expected_name:
            raise ValueError("Named-tool request did not return exactly the requested tool")
    if m.tool_calls:
        args = json.loads(m.tool_calls[0].function.arguments)
        definition = next((t["function"] for t in tools
                           if t["function"]["name"] == m.tool_calls[0].function.name), None)
        if definition is None:
            raise ValueError("Response called an undeclared tool")
        # Validate the complete envelope before unwrapping the answer. Otherwise
        # required or forbidden top-level arguments could escape the check.
        Draft202012Validator(definition["parameters"]).validate(args)
        return args
    return None


def call(messages, capture_name, **kw):
    body = {"model": MODEL, "messages": messages, "temperature": 0.6, "top_p": 0.95,
            "max_tokens": MAXT, "extra_body": EXTRA, **kw}
    if CAPTURE:
        # Synthetic prompts only; record the exact request settings before the call so a
        # failure still has them, and never write credentials (redact is the guard).
        (CAPTURE / f"{capture_name}-request.json").write_text(json.dumps(
            {"probe": PROBE_SETTINGS, "request": redact(body)}, indent=2, ensure_ascii=False))
    t0 = time.monotonic()
    r = client.chat.completions.create(**body)
    s = time.monotonic() - t0
    if CAPTURE:
        # Synthetic prompts only; save the response before parsing can fail.
        (CAPTURE / f"{capture_name}.json").write_text(r.model_dump_json(indent=2))
        (CAPTURE / f"{capture_name}-timing.json").write_text(json.dumps({"request_seconds": s}))
    if r.choices[0].finish_reason == "length":
        raise ValueError(f"generation truncated at max_tokens={MAXT}; raw response saved when CAPTURE_DIR is set")
    m, n = r.choices[0].message, (r.usage.completion_tokens if r.usage else 0)
    args = parse_tool_arguments(m, kw.get("tools", []), kw.get("tool_choice"))
    if args is not None:
        value = args.get("answer", args) if "answer" in PARAMS.get("properties", {}) and "answer" in args else args
    else:
        text = (m.content or "").split("</think>")[-1].strip()
        value = json.loads(text) if text else None
    return value, r.choices[0].finish_reason, n, s


PROSE_CASES = {"D", "F", "G"}
LONG_OBLIGATIONS = {
    "blocks": (6, 8),
    "clinical_interpretation_blocks": 2,
    "clinical_interpretation_source_ids": (1, 2),
    "followups": 2,
}


def long_obligation_failures(value, obligations=LONG_OBLIGATIONS):
    """Name the long-prompt prose obligations the enforced schema cannot express.

    The schema is the grammar; these come from the prompt text, so callers report
    them next to schema validity rather than treating them as schema errors.
    """
    blocks = value.get("evidence_review") if isinstance(value, dict) else None
    if not isinstance(blocks, list):
        return ["no evidence_review array"]
    failures = []
    low, high = obligations["blocks"]
    if not low <= len(blocks) <= high:
        failures.append(f"{len(blocks)} evidence_review blocks, expected {low}-{high}")
    clinical = [b for b in blocks if isinstance(b, dict) and b.get("basis") == "clinical_interpretation"]
    expected = obligations["clinical_interpretation_blocks"]
    if len(clinical) != expected:
        failures.append(f"{len(clinical)} clinical_interpretation blocks, expected {expected}")
    for position, block in enumerate(clinical, 1):
        ids = block.get("source_ids")
        present = {i for i in ids if isinstance(i, int)} if isinstance(ids, list) else set()
        missing = [i for i in obligations["clinical_interpretation_source_ids"] if i not in present]
        if missing:
            failures.append(f"clinical_interpretation block {position} missing source_ids {missing}")
    followups = value.get("followups", [])
    count = len(followups) if isinstance(followups, list) else None
    if count != obligations["followups"]:
        failures.append(f"{'non-array' if count is None else count} followups, expected {obligations['followups']}")
    return failures


def judge(value, schema, min_blocks=1, obligations=None):
    """Legacy structural gate, plus an optional stricter prose-obligation audit.

    ``min_blocks`` is the original minimum-block metric kept for historical
    comparability. ``obligations`` (see ``LONG_OBLIGATIONS``) adds the prompt's
    prose requirements; schema validity is decided first and reported alone.
    """
    if isinstance(value, str):
        return False, "value returned as a raw string (grammar not applied)"
    if value is None:
        return False, "no JSON content"
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        return False, f"{len(errors)} schema errors, first: {errors[0].message[:90]}"
    if obligations is not None:
        failures = long_obligation_failures(value, obligations)
        if failures:
            return False, "; ".join(failures)
    elif isinstance(value, dict) and "evidence_review" in value and len(value["evidence_review"]) < min_blocks:
        # Original minimum-block metric, unchanged for historical comparability.
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
    obligations = LONG_OBLIGATIONS if label[0] in PROSE_CASES else None
    legacy_results, strict_results, speeds = [], [], []
    for run_index in range(RUNS):
        try:
            value, finish, tokens, s = call(messages, f"{label[0]}-{run_index + 1}", **kw)
            legacy = judge(value, schema, min_blocks)
            strict = judge(value, schema, min_blocks, obligations) if obligations else legacy
            speeds.append(tokens / s if s else 0)
        except Exception as e:  # malformed arguments or content
            legacy = strict = (False, f"{type(e).__name__}: {str(e)[:90]}")
        legacy_results.append(legacy)
        strict_results.append(strict)
    legacy_passed = sum(ok for ok, _ in legacy_results)
    strict_passed = sum(ok for ok, _ in strict_results)
    if enforced and strict_passed < RUNS:
        failed = True
    speed = f"{statistics.median(speeds):.0f} tok/s" if speeds else "-"
    legacy_note = f"  legacy-minimum-blocks {legacy_passed}/{RUNS}" if obligations else ""
    print(f"{'PASS' if strict_passed == RUNS else 'FAIL'} {strict_passed}/{RUNS}  {speed:>9}  {label}{legacy_note}")
    for (legacy_ok, legacy_why), (strict_ok, strict_why) in zip(legacy_results, strict_results):
        if legacy_ok and strict_ok:
            continue
        if not legacy_ok:
            print(f"        {legacy_why}")
        if not strict_ok and strict_why != legacy_why:
            print(f"        audit: {strict_why}")
sys.exit(1 if failed else 0)
