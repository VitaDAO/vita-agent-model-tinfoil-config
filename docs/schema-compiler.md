# Fable serving compiler correction — task #3

## Problem and boundary

The pinned SGLang image uses XGrammar 0.2.1. Its JSON-schema converter parses
`anyOf` alternatives without their sibling requirements, ignores `contains`,
and generates unrestricted JSON for a multiple-branch `allOf`. Native tests
reproduce these failures without Fable, model weights, CUDA, or DFlash.

This correction belongs to the model **serving layer**. It does not retrain
Fable, change the draft, modify the agent's schema, repair generated text, or
establish clinical accuracy. The original handoff schema is preserved in
`tests/serving/final_answer_schema.json`. The native fixture must equal its
parameters exactly; a separate Draft 2020-12 validator checks the case labels.

Canonical task: https://github.com/VitaDAO/vita-agent-model-tinfoil-config/issues/3

## Implementation

- Conjoin shared constraints before compiling alternatives, retaining required
  fields, types, finite values, bounds, and closed-object semantics.
- Support exclusive alternatives only when their languages are provably disjoint.
- Compile finite-enum/const `contains` predicates into counter states. Each
  predicate counts matching items independently, including overlaps, repetitions,
  minimum/maximum counts and empty arrays. Token masks and rollback use the
  existing matcher. Impossible alternatives are identified during parsing.
- Reject unsupported substantive intersections explicitly instead of silently
  generating a weaker grammar. Preserve no-op/annotation conjunctions so existing
  references, patterns, and formats still take their original parser paths.
- Deliver the caller's `response_format.json_schema` to the standard Jinja
  renderer as model context. Grammar enforcement remains unchanged. Specialized
  encoders retain their existing rendering, and tool-only requests retain their
  original messages. No new health instructions or completion repair is added.

This is a bounded extension, **not full JSON Schema support**. General contains
predicates/items, positional tuple intersections, enum/const arrays combined with
contains, unproven overlapping exclusive unions, and prohibited optional fields
in open intersections are unsupported. Existing upstream restrictions outside
the corrected paths remain. Finite contains compilation has a 65,536-state
resource limit and returns an explicit error if exceeded.

## Reproduce locally

No model endpoint or data credentials are required:

```sh
uv run --no-project --with jsonschema==4.25.1 python tests/serving/check_fixtures.py
uv run --no-project --with cmake==3.31.10 python scripts/build_schema_compiler.py
```

The script creates a new disposable directory, checks out upstream commit
`5b4e9ce9e72524037ae24ecd831b9b6604d2eb48`, verifies its identity, applies the
native source correction, and runs the complete C++ test executable. `--baseline`
omits the correction and intentionally fails the regression tests.

`docker/schema-compiler/xgrammar-0.2.1.patch` changes the pinned upstream source;
`schema_conjunction.h` contains its conjunction support. This is a build-time
source change, not a runtime output-repair path. The build manifest records both
file hashes. The resulting package version is `0.2.1+vita4`. The September 12 correction also
rejects closed objects whose required property has no allowed declaration, while
preserving an independently valid null alternative. See
[the RunPod evaluation](runpod-serving-benchmark-2026-09-12.md) for current evidence.

## Candidate image and remaining release gates

`docker/Dockerfile.schema-compiler` derives from the exact live image digest
`sha256:64e4914593690fbba806f19ac8b2237f78815e9046dd2fc874f630129f281607`.
It builds and tests the compiler, replaces its wheel in the runtime, and tests
the installed Python binding plus SGLang's nonstreaming/incremental Qwen parser.
It also applies the hash-checked rendering correction to pinned SGLang source
and runs the rendering tests against that installed source. Model packages remain unchanged. The configuration additionally enables native
strict thinking and a 4,096-token reasoning budget, as documented below. These installed-image checks require a Linux image
build; local C++ tests do not substitute for them.

At the September 12 release-preparation check, the live release was `v0.10.1`: serving image
`sha256:47403a0af08f629a55fd65695bb250f378e9938c358b795ae50c1c38ad9cfe08`, built from
`9ebdc612db3fd351457dbe96991bf9e331f31ace` with compiler `0.2.1+vita3` and attested
as `8ff0ca151438710252055cb5a7b8b3cd242b6749564006fb09143c9fc2704788`. The proposed
`v0.10.2` release pins
`sha256:2fd6c1db75a33fe55a491e09930a87118aa39cc402b67cfe6281028c88d6c719`, built from
`879a16a3fced261856c08cbf5be0dc57daf9dfa2` with compiler `0.2.1+vita4`. `v0.10.1`
remains the rollback target. Live deployment evidence is recorded in [issue #3](https://github.com/VitaDAO/vita-agent-model-tinfoil-config/issues/3).

The dedicated workflow runs native checks on a PR. Image publication is manual
and emits an immutable digest; it has no deploy action. GitHub requires a new
manual workflow to exist on the default branch before it can be dispatched.
The existing slim-image and Tinfoil attestation workflows are untouched.

Before release:

1. Finish independent review, Linux native checks and the installed-image build.
2. Record source revision, image digest, test configuration/enclave and rollback.
3. On a separate H200 test enclave, keep target/draft weights, DFlash, scheduling
   and sampling identical to the baseline. Separately record the native thinking
   configuration experiment and its quality/performance results.
4. Run the unchanged handoff probe A–F five times each with default thinking;
   also inspect required-tool and streaming behavior. No partial-JSON repair.
5. Run the same single-stream protocol as the baseline. Decode throughput must
   remain within 10%; report first-token and total latency separately. Local
   compiler timings are not inference-speed evidence.
6. Record the principal's release authorization, pin/attest the
   tested digest, select the proposed `v0.10.2` tag with the Tinfoil CLI, then verify
   attestation, health, and synthetic smokes. Retain `v0.10.1` as the rollback
   target and execute recovery only under the recorded release recovery plan.

Production promotion is proven by the recorded live attestation and synthetic smoke,
not a source commit. Clients that do not request strict decoding must opt in to use
contract enforcement. This task does not promise to remove every application
timeout, unsupported inference, or recall failure.

A `compiler-build-*` tag can explicitly build an immutable candidate from a reviewed task commit before this workflow reaches the default branch. This tag does not match the Tinfoil attestation workflow's `v*.*.*` trigger and does not deploy or select a model release. PR runs remain verification-only.

### Deliberate reference-support boundary

Conjunctions that contain both resource identifiers (`$id`/`$anchor`) and references are rejected, including identifiers in unused definitions. The pinned resolver has no resource-scope stack; attempting to exempt apparently unrelated identifiers admitted incorrect values through reachable definitions. This conservative rejection is intentional until resource-aware resolution is implemented separately. Ordinary references without resource identifiers and identifier-bearing finite values remain supported. The original Vita answer contract contains no resource identifiers, so this boundary does not restrict its acceptance cases.

Review disposition: the request to accept unused identifier/reference combinations is deferred as a compatibility extension. Reverting that extension fixes the demonstrated invalid-value acceptance. That correction is carried by the live `v0.10.1` image `47403a0af08f629a55fd65695bb250f378e9938c358b795ae50c1c38ad9cfe08`, built from `9ebdc612db3fd351457dbe96991bf9e331f31ace`; the proposed `v0.10.2` image `2fd6c1db75a33fe55a491e09930a87118aa39cc402b67cfe6281028c88d6c719` retains it and was built from `879a16a3fced261856c08cbf5be0dc57daf9dfa2`.

### Earlier compiler and renderer diagnostics on September 11

The compiler-only test image was attested and exercised on H200 with DFlash.
The original short contract passed 0/5 on the baseline and 4/5 on the
candidate. The remaining captured failure reached `max_tokens=7000` while
repeating permitted source IDs. Exposing the contract in model context passed
5/5 in a controlled diagnostic; the Jinja correction implements that exact
context delivery inside the server.

Long diagnostic cases remain **unaccepted**: JSON output passed 4/5 (one turn
used 6,778 thinking tokens and exhausted its 7,000-token total), and named-tool
output passed 3/5 (two completed values contained fewer blocks than requested).
Every captured completed value conformed to the contract, which does not itself
require the prompt's six blocks. Structural conformance, instruction following,
and completion within the token limit must be reported separately. These
synthetic prompts supply source identifiers without the underlying measurements;
they are not a clinical-quality oracle. The acceptance gate is not relaxed.

`scripts/install_response_contract.py` checks the exact upstream source hash
before editing. `tests/serving/test_response_contract.py <serving_chat.py>` runs
the production Jinja and continuation methods with a recording tokenizer. Four
checks fail on unmodified pinned source; all six pass with contract delivery.
Those tests prove rendering, not successful GPU completion. The later H200
results and native thinking configuration are recorded below.

## Production-directed configuration after test v0.5.95

The tested configuration is test repository commit
`7cf1e5e5fb5538de05b17094823d36f1cc7f4e43`, tag `v0.5.95`. This release copies its
configuration exactly, including image digest
`47403a0af08f629a55fd65695bb250f378e9938c358b795ae50c1c38ad9cfe08`,
`--enable-strict-thinking`, and `SGLANG_MAX_THINK_TOKENS=4096`.
Pinned SGLang is `db272201a2dbd72e5699e443240a851f1313ad45`.
The target, draft, eight speculative tokens, H200 allocation, scheduling and
sampling settings remain unchanged.

The unchanged A-F probe returned 28/30 passes. All 30 outputs were complete and
contract-valid; two F answers contained only an acknowledgment rather than six
to eight blocks. None exhausted the 7,000-token total budget. Several D/F
requests reached the native reasoning boundary and then produced valid final
answers. The usage counter includes the closing marker (4,097 reasoning tokens).
Explicit request budgets of 32 and 128 tokens also ended thinking correctly and
returned valid JSON. Streaming and two simultaneous distinct contracts passed.

The matched benchmark median was 127.5 tokens/sec versus 162.2 baseline:
21.4% slower, outside the original 10% target. Strict thinking creates a grammar
object even for otherwise unconstrained requests; tracing and removing that
additional cost is follow-up work. The source-only compiler/rendering image
with prior launch settings measured 161.7 tokens/sec but still allowed a
request to exhaust its thinking budget.

These failed instruction-following and speed targets are retained in the record.
After their disclosure, the principal directed this candidate to be the new
production model. This is a directed release with known limitations, not a claim
that the original acceptance gates passed. See task #3 for the active message
receipt, production commit/tag/attestation, and final live verification.

No application client schema, source data, model weights, or sampling settings
are changed by this promotion. Strict contract enforcement applies when the
caller requests JSON-schema/strict tool decoding. It does not establish factual
accuracy or clinical applicability of an otherwise well-formed answer.
