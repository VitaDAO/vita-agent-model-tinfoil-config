# Vita agent model: RunPod serving evaluation, 12 September 2026

Task: [#3](https://github.com/VitaDAO/vita-agent-model-tinfoil-config/issues/3). Implementation: [PR #5](https://github.com/VitaDAO/vita-agent-model-tinfoil-config/pull/5).

## Decision and scope

Keep Fable FP8 with the pinned DFlash2 FP8 draft, block size 8, native strict thinking and its existing 4,096-token maximum. Retain the bounded compiler correction as the release candidate; the full answer-quality gate remains open. Do not select a globally tiny thinking budget for evidence synthesis. Complete source content and an explicit inference policy improved the tested answers; a schema alone cannot guarantee grounded interpretation. The later immutable-image experiment below supports testing a separate concise profile with a 512-token budget, with a larger budget retained for complex answers.

An experimental SGLang mask-skipping change was implemented, independently reviewed, tested, and **removed from the release proposal**. Its small difference was within the observed throughput variation. The final proposal does not add that serving path, installer, or its experimental tests. It also does not change model weights, the Tinfoil environment, the agent's schema or prompt, or application routing.

This is a model-serving evaluation using synthetic fixtures. It is not the application’s 20-question battery, database/conversation recall evaluation, clinical validation, a 10,000-user capacity test, or proof of a perfect model.

## Reproducible identities and conditions

- Source base: `8db8761084bd5262e8960042f25983d179b4c781`.
- Final runtime/build source: `879a16a3fced261856c08cbf5be0dc57daf9dfa2`; later test/report revisions do not change runtime inputs.
- Baseline serving image, which is the live `v0.10.1` serving image: `ghcr.io/vitadao/vita-agent-model-sglang@sha256:47403a0af08f629a55fd65695bb250f378e9938c358b795ae50c1c38ad9cfe08`.
- SGLang: `db272201a2dbd72e5699e443240a851f1313ad45`.
- Target: `alexdobrin/Qwen3.8-27B-Fable-Distill-FP8@dad2544d418ffb797af211fb49e4dd770af8e33f`.
- Draft: `incoai/Qwen3.8-27B-DFlash2@dedf8df68adfb1afeaf7b7480c0a0243108177b4`.
- Compiler baseline `0.2.1+vita3`; candidate `0.2.1+vita4`. The first session's pre-image GPU experiment used a separate local build of the candidate, wheel SHA256 `a92cbf3065b9fbed01a8a946e2fee7cda331440a23676735b4e6f1f02747aef8`. The immutable-image candidate embeds a distinct build of the same pinned source, `xgrammar-0.2.1+vita4-cp312-cp312-linux_x86_64.whl` SHA256 `f4873dda1e0493c4c4b1f62546639a975f4f1a9c7e1c48d69e2971d006d33f9a`; see the final section. Wheel bytes are not reproducible across builds, and the two wheels carry the same source inputs. Model weights are unchanged.
- One RunPod Secure Cloud H200, 143,771 MiB GPU memory, driver 580.178.04, CUDA 13, 16 vCPU, host RAM advertised as 251 GB (cgroup `memory.max` 250,999,996,416 bytes, about 233.8 GiB). GPU observations: 1,980 MHz SM, 3,201 MHz memory.
- FA3; page size 64; static memory fraction .85; context 262,144; chunked prefill 4,096; Mamba `extra_buffer`, ratio .2, SSM bfloat16; requested concurrency 16, runtime cache allocation capped effective running requests at 14; CUDA graph decode maximum 16.
- Inference bound to pod loopback. Benchmarks below use a client on that pod. SSH-tunnel runs are retained separately because their network latency is not comparable.
- No real personal/health data; no public inference listener. Credentials were not committed or placed in result files.

The initial RunPod session used the pinned baseline image with the candidate compiler installed into an isolated Python path. The second session, documented under "Immutable-image acceptance", booted the final immutable image directly with no runtime compiler replacement. Tinfoil confidential-compute, storage, extraction, CPU and network differences prevent attributing a RunPod-versus-Tinfoil difference to this compiler change.

## Correctness fix

The old compiler accepted `{}` for an impossible closed object that required an undeclared property. In a nullable version it also accepted `{}`, although only `null` was valid. This was reproduced without changing Fable weights or the draft.

| Request | Baseline | Candidate |
|---|---|---|
| Closed object requires undeclared property | HTTP 200, invalid `{}` | HTTP 400, impossible grammar rejected |
| Same object with independent null alternative | Invalid `{}` | Valid `null` |
| Required property declared and permitted | Valid object | Valid object |

The result also passed with speculation disabled. The correction rejects the impossible object branch while preserving a valid null branch. It does not claim full JSON Schema support; the documented conjunction, reference and `contains` limitations remain. `patternProperties` interactions beyond this bounded correction remain outside this acceptance claim.

Verification includes 68 native compiler tests, 24 independent Draft 2020-12 fixture expectations, installed-wheel JSON/Qwen/incremental parser checks, and live negative probes. The benchmark harness now rejects missing terminal events, missing token accounting, in-band errors, incomplete stop responses, wrong/duplicate named tools and invalid full tool envelopes. Seven stream tests and twelve envelope, prose-obligation and request-capture tests cover those boundaries.

## Throughput experiments

Same pod and pinned weights; temperature 1.0, top-p .95, top-k 20 for the sustained essay benchmark; maximum 2,048 completion tokens. Rates are median reported completion tokens divided by the streamed decode interval and include reasoning. A token-limit finish is retained as a performance observation, not an answer-quality pass.

| Variant | Repeats | Median decode tokens/sec |
|---|---:|---:|
| Baseline, FP8 target + FP8 DFlash2 draft | 5 | 200.1 |
| Compiler candidate + experimental masks, FP8 draft | 7 | 197.8 |
| Same candidate, BF16 draft | 7 | 196.0 |
| Same candidate, no DFlash | 7 | 101.8 |

DFlash2 approximately doubled throughput in this workload (1.94×). BF16 draft offered no demonstrated advantage. The compiler/mask candidate showed no single-stream speed improvement over baseline; its difference was within the original 10% regression allowance. Do not claim that changing the compiler alone accelerates decoding.

An initial mixed-length concurrency run suggested 751 versus 583 aggregate tokens/sec. Those answers had different generated lengths, so that comparison is **not accepted as a causal speedup**. A fixed-load A/B/A experiment followed: eight requests per batch, concurrency four, exactly 2,048 completion tokens per request, three batches per phase, reasoning budget 128. EOS was intentionally ignored to equalize the workload; these forced-length outputs are not quality evidence.

| Mask phase | Batch rates (tokens/sec) | Median |
|---|---|---:|
| On, first | 989.2 / 967.1 / 1,020.6 | 989.2 |
| Off | 967.6 / 971.0 / 1,012.4 | 971.0 |
| On, repeated | 983.4 / 1,003.5 / 1,013.8 | 1,003.5 |

The 1.9–3.4% median difference is smaller than the observed spread; three batches cannot establish a dependable benefit. The additional patch was removed rather than promoted on an uncertain performance claim.

## Reasoning budgets and answer quality

A balanced test rotated four budgets across five prompts and four repetitions: arithmetic, explicitly supplied note retrieval, population-versus-individual inference, study cutoff-versus-treatment target, and an open grounded summary. The first four have exact answer oracles; the fifth needs manual review. Prompt ordering was counterbalanced, but this is still a small repeated synthetic workload with warm caches.

Initial balanced candidate run:

| Reasoning budget | Mean complete response | Mean first visible answer |
|---|---:|---:|
| Default, maximum 4,096 | .707 s | .645 s |
| 32 | .328 s | .160 s |
| 128 | .435 s | .306 s |
| 512 | .611 s | .544 s |

All 64 exact-answer checks, completion checks and explicit budget checks passed. The 16 open summaries were 115–153 words, within the requested 100–160 words. Manual review nevertheless found unsupported variability adjectives at budgets 32 and 512, and occasional advice extending beyond the supplied association. The run is **not 80/80 factual accuracy**. A lower cap reduces waiting on easy prompts; it does not establish safe evidence synthesis or predict full application latency.

Long structured-answer probes used the unchanged six-case handoff and five repeats. Baseline passed 28/30 and the initial compiler/mask candidate 29/30 under the original schema/minimum-block gate. Remaining long-tool outputs contained only one answer block. The schema allows fewer blocks than the prompt asks for; structural validity does not enforce all prose instructions. These original sparse-source failures are retained, not replaced with easier fixtures.

A separate complete-source fixture supplied six synthetic personal measurements and a synthetic observational study. All ten initial outputs passed the structural/minimum-block gate, but manual review found absent seven-hour thresholds, unsupported ranking and inference from sleep efficiency. Test-only grounding instructions removed much of this. The final example explicitly separates observation from intervention and source-topic coverage from comparative importance.

With that example and default thinking, the first ten long outputs passed the structural/minimum-block checks and manual review found none of the tested unsupported thresholds, grading, intervention or priority-ranking claims. This is a small in-sample result, not a generalized accuracy guarantee. At budget 512, two of five long-tool repeats returned only one answer block; some other answers exceeded the requested upper block count. That cap is not recommended for complex synthesis. The test policy is not injected into production serving; an agent integration would require separate application QA.

## Context, memory and resource limits

The synthetic long-context probe recovered three exact records placed near the beginning, middle and end in all four runs. Actual input sizes were 11,084 and 87,548 tokens, despite approximate workload labels of 8K and 64K. The larger input took 7.97 seconds on its first run and 1.22 seconds on a cached repeat. Budget and cache state differ; this measures cold/warm context effects, not a budget speedup.

This is in-context retrieval, not database recall, conversation indexing, tenant isolation or compaction. It does not prove 262K context at maximum concurrency. Runtime GPU memory was about 125,492 MiB; whole-pod cgroup peak host memory was 39.8 GiB during the recorded experiment. That does not establish that Tinfoil can extract and start the image with 64 GiB, or that smaller RAM supports every load. Cached restarts still took about 151–171 seconds, principally model/kernel/graph startup; keep instances warm when low request latency matters.

## Remaining delivery boundaries

The compiler correction is source- and GPU-tested. The final immutable image, compiler-only repeat results and paid-pod cleanup are recorded below. At the end of these experiments, no production environment had been selected or redeployed: the live release remained `v0.10.1` (attested digest `8ff0ca151438710252055cb5a7b8b3cd242b6749564006fb09143c9fc2704788`), the candidate was prepared for `v0.10.2` but had not been deployed, and `v0.10.1` was selected as its rollback target. Later deployment evidence is tracked in issue #3. The canonical issue remains open for the unresolved general instruction-following and application acceptance scope.

Reproduce the local checks with the pinned environment described in `schema-compiler.md`. Against an isolated authenticated test endpoint, run `probe_object_closure.py`, the original `probe_constrained.py` with `CASES=ABCDEF RUNS=5`, the separately labeled complete-source/policy probes, `benchmark_profiles.py --repeats 4`, and `benchmark_fixed_load.py`. Use synthetic inputs, preserve raw outputs for manual review, and shut down paid infrastructure after the bounded evaluation.


## Final compiler-only verification and cleanup

Final candidate image:
`ghcr.io/vitadao/vita-agent-model-sglang@sha256:2fd6c1db75a33fe55a491e09930a87118aa39cc402b67cfe6281028c88d6c719`.
Built from `879a16a3fced261856c08cbf5be0dc57daf9dfa2` in successful workflow
[34693466934](https://github.com/VitaDAO/vita-agent-model-tinfoil-config/actions/runs/34693466934).
Native PR checks also passed in run 34693469203. The earlier experimental image
`c1cbe0f1aa6ad658229327db88f32ac2d6f15df5e1cef31e3cb7b71e7302d178`
contains the removed mask experiment and is not the recommended image.

The immutable image embeds `xgrammar-0.2.1+vita4-cp312-cp312-linux_x86_64.whl`,
SHA256 `f4873dda1e0493c4c4b1f62546639a975f4f1a9c7e1c48d69e2971d006d33f9a`, 45,881,371
bytes. It is a separate build from the pre-image experiment wheel `a92cbf30…`, and
both record the same source inputs: `upstream_revision 5b4e9ce9…`,
`patch_sha256 c0fbed69…` and `header_sha256 d10113ae…` in the image's
`compiler-build.json`. No model weights changed.

Final RunPod runtime: compiler 0.2.1+vita4, SGLang 0.0.0.dev1+gdb272201a,
PyTorch 2.13.0+cu130, Transformers 5.12.1. All four SGLang mask-source hashes were
verified equal to their original pinned upstream bytes after the experiment.
Cached startup took 156.19 seconds. Closed-object rejection also passed with the
compiler's strict_mode both false and true.

- Final sustained essay: five runs at 189.6 / 195.6 / 196.7 / 203.7 / 194.6 tokens/sec,
  median **195.6**, first-token latency .05–.08seconds on pod loopback.
  This is 2.25% below the 200.1 baseline, not a raw decoding speed improvement.
- Original handoff: **29/30** under its schema/minimum-block gate. All five
  A–E repetitions passed; F passed 4/5, with one one-block answer.
  The original acceptance failure remains open.
- Complete-source/default-thinking/policy repeat: **10/10** structural and
  minimum-block checks; all 10 had 7–8 blocks and two followups. Manual review
  found none of the targeted unsupported seven-hour thresholds, user-value
  grading, intervention benefit, or clinical priority ranking. Residual wording:
  one answer called the study "small" without a stated comparison, and another
  implied identical study/personal variation measures although the synthetic
  study does not specify its exact statistic. Broader semantic accuracy remains
  unproven; this is not ten perfect answers.
- Final balanced 80: **64/64 exact answers**, **60/60 explicit reasoning-budget
  checks**, all 80 completed. Sixteen summaries were 110–159 words. They still
  occasionally added unsupported qualifiers; one 32-budget summary called heart
  rate and steps "within common ranges" without a supplied range. This rejects
  a blanket fast-budget or blanket accuracy recommendation.

| Final budget | Mean completion | Mean first visible answer |
|---|---:|---:|
| Default maximum 4,096 | 1.016s | .950s |
| 32 | .320s | .151s |
| 128 | .429s | .300s |
| 512 | .623s | .557s |

The final default profile varied from the earlier .707s average because generated
reasoning and text differ. Repeated synthetic prompts are not a production latency
SLO. Budget selection and explicit inference policy should be evaluated together
in the agent, retaining adequate reasoning for complex synthesis and confirming
that the application actually sends the structured contract and complete evidence.
That application integration was not changed in this model-serving task.

The raw synthetic results, manifests and runtime versions were downloaded and
SHA256-verified before teardown. Archive digest:
`b15f44cb3f6faf8988c0e45cc5e74d9d608df2697d3271ab0140ce6323b7019c`.
The executed essay script differs from the final repository version only in draft
counter naming; its terminal-event, visible-answer and actual
usage-token accounting checks were present during these runs. The exact executed
script is retained with the local results.

The owned GPU was terminated through RunPod at **12:33:32 UTC**, and its absence was
verified in the pod list. SSH tunnel and cleanup watchdog exited. The instance
existed for approximately 94 minutes at $4.59/hour: approximately **$7.17** in listed
compute-rate time, not a reconciled invoice. No persistent volume was provisioned.
RunPod credentials remain in the user's macOS Keychain. Tinfoil production is
unchanged. At the end of that first session, the newly published image had passed
installed-container build checks but had not been GPU-booted. The second session
below closes that RunPod boot gap; Tinfoil attestation/deployment remains separate.

## Immutable-image acceptance: 14:18–14:58 UTC

The second owned H200 booted the final `2fd6c1db…` image directly, with the same
target/draft revisions, DFlash2 FP8, block size 8 and runtime settings above.
There was no wheel swap or `PYTHONPATH` override. The installed compiler check,
live impossible/nullable/declared-object probes and a streamed named-tool probe
passed. The latter returned the exact permitted arguments despite an adversarial
request for forbidden values, with `tool_calls` finish and positive usage.
Native request-level `custom_params.thinking_budget` was verified in source and
in the measured reasoning counts; no additional serving adapter is necessary.

The refreshed tests ran from `fad000beecb93a2e307e81f05b040cc4539a17f2`;
the separately added `probe_brief_grounded.py` is retained with the artifacts.
All requests and responses are synthetic. The original prompts and schema were
preserved. The audit additionally checks the long prompts' six-to-eight blocks,
two interpretation blocks referencing both source IDs, and exactly two followups.
These requirements are in prose, not all enforced by the original schema.

### Original handoff and controlled long-answer comparison

The original six cases, five repeats each, returned **30/30 schema-valid
responses**, **27/30 under the legacy minimum-block gate**, and **25/30 under
the fuller prose-obligation audit**. A/B/C/E passed 5/5 each; D passed 4/5 and F
1/5 under the fuller audit. The original requests retained `max_tokens=7000`.
The earlier 29/30 result was a different stochastic run under the weaker gate;
it is not comparable to 25/30 as a serving regression measurement.

Manual review matters: the original fixture names sources without supplying
their measurements or study findings. Two short F answers correctly declined
to infer missing information; they fail the requested length but are appropriate
limitations. Another promised six readings without providing them. Two invented
study findings or advice, including the F answer that passed the block audit.
Neither schema validity nor a block count is an accuracy score.

The following 40-request experiment used the unchanged original tool schema,
`max_tokens=12000`, temperature .6, top-p .95, top-k 20, five repeats per condition,
with rotated/reversed condition order. Complete sources add six synthetic
measurements and an explicitly synthetic observational study. "Policy" adds the
test-only grounding instructions, never a server-injected health policy.

| Source content | Test policy | Thinking maximum | Prose checks | Mean request time |
|---|---|---:|---:|---:|
| Source names only | Off | Default 4,096 | 3/5 | 18.98 s |
| Complete | Off | Default 4,096 | 5/5 | 14.19 s |
| Source names only | On | Default 4,096 | 1/5 | 17.60 s |
| Complete | On | Default 4,096 | 5/5 | 14.89 s |
| Source names only | Off | 8,192 | 5/5 | 26.48 s |
| Complete | On | 8,192 | 5/5 | 16.93 s |
| Complete | On | 512 | 4/5 | 4.58 s |
| Complete | On | Thinking off | 3/5 | 3.93 s |

All 40 requests ended in a named tool call. More thinking with missing sources
produced more complete-looking but unsupported answers. Complete sources alone
still permitted unsupported grading and priority ranking. In the complete-source
plus policy answers, manual review found no invented measurements, clinical
thresholds or intervention benefits at default or 8,192; residual issues included
repeated limitations and source-derived conclusions labeled as general claims.
At 512, one answer returned only the personal measurements and omitted the
literature synthesis. With thinking off, one answer called variability "large"
without a supplied comparator; others exceeded the requested block count.
Increasing the budget is not a substitute for missing evidence, and disabling
thinking is not an accepted general solution.

### Separately labeled concise-answer diagnostic

`probe_brief_grounded.py` keeps the same tool schema, complete sources and policy
but requests two-to-four text blocks, at most one followup, and at most 150 words
across displayed claims and followups. It is an additional diagnostic, not a
replacement for the failing original long cases. Five repeats per budget were
counterbalanced, with the same 12,000-token output allowance and sampling.

| Thinking maximum | Format and length | Mean request time | Mean displayed words | Word range |
|---|---:|---:|---:|---:|
| Default 4,096 | 5/5 | 10.15 s | 121.6 | 110–130 |
| 512 | 5/5 | 2.77 s | 115.0 | 97–132 |

That is a **72.7% reduction in mean waiting time** within this concise fixture.
All ten final answers were manually compared with the supplied synthetic sources:
none invented measurements, clinical cutoffs, comparative priority rankings or
individual intervention benefits. Both the measurements and observational-study
limits remained present. Minor wording issues remain: "cited study" sometimes
appears despite this synthetic fixture having no publication/citation identifiers,
and some coverage limitations recur. This ten-answer, in-sample check does not
establish general clinical quality or prove that a 512 budget works on other tasks.

### Speed, startup, artifacts and cleanup

Five fresh sustained essays measured 202.0 / 197.6 / 201.1 / 206.0 / 195.6 decode
tokens/sec: median **201.1**, versus the earlier baseline 200.1. First-token
latency was .05–.08 s on pod loopback. These naturally completed essays have
different lengths; the near-equal rate demonstrates no material observed
regression, not a compiler-caused speedup. Most of the concise response-time
improvement above comes from less reasoning/output work, not faster raw decoding.

Server launch to first healthy response took 288.55 seconds, including model,
kernel and CUDA graph initialization. Image extraction and download preceded
that interval. GPU allocation was 125,494 MiB and whole-pod host-memory peak
39.16 GiB. This remains a RunPod host experiment with the advertised 251 GB host
RAM (cgroup `memory.max` 250,999,996,416 bytes, about 233.8 GiB); it does not prove
that Tinfoil image extraction will fit 64 GiB or establish full-context concurrency.

An initial setup command referenced a fixture at the wrong local path and stopped
before inference. It was corrected to use the image's installed compiler check;
both the failed setup log and the successful rerun are retained. No runtime image
or model change was needed for that test-invocation correction.

Full synthetic results, exact request/response bodies, execution logs, launch
configuration and stream probe were downloaded and hash-verified before deletion:
archive SHA256 `d7bd0ba23f5fd96853bebb2a509f06bf5064ae52bf350ddee9c0500da1627b30`.
The owned pod was deleted at **14:58:36 UTC**, verified absent, and its cleanup
watchdog exited. No RunPod pods or persistent volumes were left by this task.
This session lasted 40.16 minutes at $4.59/hour, approximately **$3.07** in listed
compute-rate time, not a reconciled invoice. Credentials remain in Keychain.

### Resulting implementation boundary

The model-serving candidate now has immutable-image GPU boot, compiler enforcement
and throughput evidence. Keep the generic server generic and retain native
DFlash2 and per-request budget control. The next application change should provide
the actual selected evidence once, carry its applicability and identifiers into
the answer, request length appropriate to the question, and reserve sufficient
reasoning for complex synthesis. Evaluate a concise/512 profile as a bounded
option alongside the default; do not infer an automatic routing policy from these
five repeated examples. Audit source-grounded conclusions separately from schema
and length checks. No output rewriting, regex filter or extra model repair round
was added in this serving work.

The application 20-question battery, database recall, production load and Tinfoil
release acceptance remain untested by this experiment. PR #5 stays draft and the
broader issue stays open. The failing original cases and remaining provenance
precision prevent calling the model or application perfect or production-accepted.
