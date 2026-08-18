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

| E6 | FP8, official `-runtime` image, 64 GiB (v0.6.2) | — | — | — | — | FAILED: same "no space left on device". The runtime variant is only 7% smaller (16.7 vs 18.0 GB compressed) |
| E7 | FP8, custom slim image (6.2 GB), 64 GiB (v0.7.0) | — | — | — | — | FAILED: enclave could not pull from ghcr.io ("context deadline exceeded" after ~35 min; the same blob pulls fine from a laptop, and Docker Hub serves 18 GB images to this enclave without trouble). ALSO contraindicated: `python:3.12-slim` has no nvcc, and SGLang JIT-compiles its KV-store kernel — failure is swallowed and falls back to a slow scatter |
| E8 | FP8, SGLang **v0.5.17** (v0.8.0) | | | | | ships #32219 "cut spec-v2 host-seam overhead in hybrid-linear MTP decode ... at low concurrency" = exactly this stack + this bottleneck |

## FINAL verified numbers — v0.6.1, re-measured 2026-08-19 with the extended protocol
```
greedy   : 140.9 tok/s (accept 2.275)
t=1.0    : 128.0 tok/s (accept 2.05), TTFT 0.26-0.29 s
agg @8   : 639.9 tok/s      <- beats the BF16 aggregate record (553.6); the earlier
                               FP8 reading of 504 was taken on a cold server
long 16k : 34,323-token prompt -> TTFT 3.36 s, decode 176.0 tok/s
           repeat (radix prefix cache warm) -> TTFT 0.67 s (5x faster), decode 176.9
quality  : PASS
```
**Decode is FASTER at long context (176 vs 141)** — with 34k tokens of context the MTP
draft head predicts far better, so acceptance rises and each forward pass yields more
tokens. Real agent traffic (long prompts, repeated prefixes) therefore runs faster than
the 800-token synthetic bench, not slower.

## RAM-minimization goal — CLOSED, 128 GiB stands
**Also economically moot:** the dashboard shows Container Usage "Billing Exempt"
($55.61 accumulated, $0.00 charged), so the RAM tier costs nothing. E7b (2026-08-19)
retried the slim image and hit the SAME ghcr timeout at 31 min — reproducible, not
transient. Note the other VitaDAO enclaves (vita-agent-prod, vita-ingest, aubrai-server)
DO pull from ghcr fine; they are small app images, so the limit is ghcr throughput vs
the pull deadline at ~6 GB, not reachability. Docker Hub serves 18 GB to this enclave.

Enclaves are diskless (containerd on a RAM-disk), so enclave RAM must hold mpk + extracted image +
runtime. 28.8 GiB mpk + ~35 GiB official image + OS does not fit 65536; the only tier between is
none (64 -> 128). Slim image fits but (a) ghcr is not reliably pullable from the enclave and
(b) without nvcc it silently loses the JIT'd KV-store kernel. Not worth the RAM.

## Research sweep (13 agents, source-level + adversarial verification, 2026-08-18)
- **KV-cache dtype: do NOT.** KV is only 64 KiB/token = ~0.2% of decode bytes here (16 full-attn
  layers x 4 KV heads x 256 head_dim), so zero bandwidth upside. `fp8_e4m3` also casts Q to FP8
  with NO descale (our ckpt has no k_scale/v_scale; `qwen3_5.py` builds RadixAttention without
  quant_config) => quality risk + 2-5% slower. `fp8_e5m2` SILENTLY rewrites attention_backend to
  triton, losing the fa3 path with no error — worst possible outcome on a log-blind enclave.
- `--quantization-param-path`: silent no-op today; guaranteed boot RuntimeError if combined with
  fp8 KV. Never set.
- `--speculative-eagle-topk 1` must stay 1: besides speed, topk<=1 gates `conv_window_dedup`,
  which halves conv-state memory across all 48 GDN layers.
- Triton blockwise-FP8 tuning is pointless: all 407 FP8 tensors satisfy N%64==0 && K%128==0 so
  DeepGEMM (a core dep, prebuilt .so) handles them; the Triton path never executes.
- Roofline at accept 2.2: 141.7 tok/s = ~41% of H200 HBM. Config-only ceiling 145-165;
  hard ceiling ~250. The two real levers are (1) accept_length 2.2 -> 3.0 = +36% (a draft-head
  quality problem, not a serving-flag one) and (2) upstream kernel work (PRs #31652, #35142).

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
