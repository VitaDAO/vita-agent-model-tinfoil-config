# fable-distill speed experiments — control.inf6 H200, 2026-08-18

Protocol per experiment: warm the server, then
(a) greedy t=0, 800 tok, 2 passes — decode tok/s (acceptance-max case)
(b) thinking sampler t=1.0/top_p .95/top_k 20, 800 tok, 3 passes — median (comparable baseline)
(c) concurrency 8 × 300 tok non-think — aggregate tok/s
Record spec_accept_length gauge after (a) and (b).

| # | config (tag) | greedy 1-str | t=1.0 med | agg @8 | accept t=1 | notes |
|---|---|---|---|---|---|---|
| E0a | llama.cpp BF16 GGUF (v0.3.0) | — | 72.7 | n/a (1 slot) | 2.13* | *from llama counters; TTFT 1.3–2.0s |
| E0b | SGLang v0.5.14, NEXTN 3/4 (v0.4.0) | 106.5 | 96.0 | 449 | ~2.05 | TTFT 0.3–0.6s; t0.6→~100; penalties hurt (93.9) |
| E1 | v0.5.16 + OVERLAP_PLAN_STREAM (v0.5.0) | — | — | — | — | CRASHED post-pool-alloc; env is the culprit |
| E1b | SGLang v0.5.16 image only (v0.5.1) | 108.2 | 98.1 | 553.6 | 1.9-2.2 | WINNER so far; TTFT 0.26-0.29s |
| E4 | v0.5.16, speculative OFF (v0.5.2) | 58.2 | 51.7 | 342.0 | n/a | MTP ~doubles single-stream even at 35% accept — llama.cpp's 50% rule doesn't hold on Spec V2 |
| E2 | v0.5.16, spec steps=5/draft=6 (v0.5.3) | 104.3 | 93.0 | 466.6 | 2.3 | sequential draft steps cost > extra accepts; 3/4 optimal |
| E5 | **FP8 quant, v0.5.16, NEXTN 3/4 (v0.6.1)** | **141.7** | **134.6** | 504.3 | 2.2 | **FASTEST single-stream.** agg@8 slightly below BF16 (504 vs 554). v0.6.0 (64 GiB) failed: containerd ramdisk lives in enclave RAM — image pull needs ~25 GiB beyond the mpk |

## Verdict (2026-08-18)
Fastest found: **FP8 + SGLang v0.5.16 + NEXTN 3/4 = 141.7 tok/s greedy / 134.6 t=1.0**
(1.85x the morning's llama.cpp 72.7). For pure multi-tenant throughput BF16 v0.5.1
holds the agg@8 record (553.6). Deployed: v0.6.1 (FP8). Quality smoke test passed
(math + reasoning_content intact; draft-head acceptance unchanged at ~2.2).
Not tried (need trained drafts that don't exist for this arch): DFlash, DSpark.

Ceiling math: BF16 55 GB/token, H200 ~4.8 TB/s → ~87 fwd/s max.
tok/s = fwd/s × accept_length. v0.4.0 measured ~47 fwd/s (54% bw util).

Out-of-scope for config experiments (need new artifacts):
- FP8 distill quant (~2× ceiling) — needs llm-compressor run + dashboard wrap.
- DFlash / DSpark — need trained draft models, none for this arch/size yet.
