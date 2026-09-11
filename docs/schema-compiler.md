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
file hashes. The resulting package version is `0.2.1+vita3`.

## Candidate image and remaining release gates

`docker/Dockerfile.schema-compiler` derives from the exact live image digest
`sha256:64e4914593690fbba806f19ac8b2237f78815e9046dd2fc874f630129f281607`.
It builds and tests the compiler, replaces only its wheel in the runtime, and
tests the installed Python binding plus SGLang's nonstreaming/incremental Qwen
parser. It does not change `tinfoil-config.yml`, model packages, launch flags,
or the live enclave. These installed-image checks require an actual Linux image
build; local C++ tests do not substitute for them.

The dedicated workflow runs native checks on a PR. Image publication is manual
and emits an immutable digest; it has no deploy action. GitHub requires a new
manual workflow to exist on the default branch before it can be dispatched.
The existing slim-image and Tinfoil attestation workflows are untouched.

Before release:

1. Finish independent review, Linux native checks and the installed-image build.
2. Record source revision, image digest, test configuration/enclave and rollback.
3. On a separate H200 test enclave, keep target/draft weights, DFlash, scheduling
   and sampling identical to the current server. Change the compiler image only.
4. Run the unchanged handoff probe A–F five times each with default thinking;
   also inspect required-tool and streaming behavior. No partial-JSON repair.
5. Run the same single-stream protocol as the baseline. Decode throughput must
   remain within 10%; report first-token and total latency separately. Local
   compiler timings are not inference-speed evidence.
6. Obtain the exact-step release approval required by the handoff, pin/attest the
   tested digest, select the new tag with Tinfoil CLI, then verify attestation,
   health, and synthetic smokes. Retain live `v0.10.0` as the recorded rollback
   until the target is rechecked immediately before release.

No fixed image has been deployed. Ordinary Vita Agent final calls currently do
not request strict decoding; their separate opt-in follows successful server
acceptance. This task does not promise to remove every application timeout,
unsupported inference, or recall failure.

A `compiler-build-*` tag can explicitly build an immutable candidate from a reviewed task commit before this workflow reaches the default branch. This tag does not match the Tinfoil attestation workflow's `v*.*.*` trigger and does not deploy or select a model release. PR runs remain verification-only.

### Deliberate reference-support boundary

Conjunctions that contain both resource identifiers (`$id`/`$anchor`) and references are rejected, including identifiers in unused definitions. The pinned resolver has no resource-scope stack; attempting to exempt apparently unrelated identifiers admitted incorrect values through reachable definitions. This conservative rejection is intentional until resource-aware resolution is implemented separately. Ordinary references without resource identifiers and identifier-bearing finite values remain supported. The original Vita answer contract contains no resource identifiers, so this boundary does not restrict its acceptance cases.

Review disposition: the request to accept unused identifier/reference combinations is deferred as a compatibility extension. Reverting that extension fixes the demonstrated invalid-value acceptance. The reviewed serving source remains `202db8d`; any subsequent documentation-only or integration commit must preserve the compiler/image identity.
