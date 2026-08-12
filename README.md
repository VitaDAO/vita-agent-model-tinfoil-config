# vita-agent-model-tinfoil-config

Attestation anchor for the `vita-agent-model` enclave on the control.inf6 H200.

Serves [`DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-MTP`](https://huggingface.co/DavidAU/Qwen3.6-27B-Fable-Fusion-711-Uncensored-Heretic-NM-DAU-MTP)
behind an OpenAI-compatible API.

## Two configs

| file | runtime | weights | concurrency |
|---|---|---|---|
| `tinfoil-config.yml` | llama.cpp | GGUF MTP-Q8_0 (30.2 GB of a 465 GB wrap) | **1 request** |
| `tinfoil-config.sglang.yml` | SGLang | BF16 safetensors (55.6 GB) | 16 requests |

`tinfoil-config.yml` is active because the GGUF is what is currently wrapped.
SGLang cannot load GGUF and vLLM's GGUF path does not cover this hybrid
Gated-DeltaNet arch, so llama.cpp is the only runtime that reads that artifact —
and its MTP support ([#22673](https://github.com/ggml-org/llama.cpp/pull/22673))
is limited to a single server slot, hence `-np 1`.

To switch: wrap `DavidAU/…-NM-DAU-MTP` at commit `5fdc5e47…` in the dashboard,
paste the `mpk` and its root hash into `tinfoil-config.sglang.yml`, swap it over
`tinfoil-config.yml`, then tag. Both expose the same paths and both return
thinking in `message.reasoning_content`, so callers need no changes.

## Deploying

Tagging `v*.*.*` runs `tinfoilsh/pri-build-action`, publishing the release that
Tinfoil verifies at deploy time.

```sh
tinfoil container create vita-agent-model \
  --repo VitaDAO/vita-agent-model-tinfoil-config \
  --tag v0.1.0
```

Later releases roll out with `tinfoil container relaunch vita-agent-model --tag <tag>`.

## Benchmarking

```sh
tinfoil container connect vita-agent-model -p 3301
python3 bench_enclave.py
```

Reports TTFT, decode tok/s, and MTP draft-acceptance counters from `/metrics`.

## Sampler settings

The author's recommended values. Callers must send these explicitly — the server
does not read them from the repo:

| | thinking | thinking (code) | non-thinking |
|---|---|---|---|
| temperature | 1.0 | 0.6 | 0.7 |
| top_p | 0.95 | 0.95 | 0.80 |
| top_k | 20 | 20 | 20 |
| min_p | 0.0 | 0.0 | 0.0 |
| presence_penalty | 0.0 | 0.0 | 1.5 |
| repetition_penalty | 1.0 | 1.0 | 1.0 |

**MTP constraint:** keep `temperature <= 1.0` and `repetition_penalty` at `1.0`.
Raising either collapses draft-token acceptance and speculative decoding turns
into a net loss. Below ~50% acceptance the non-MTP quants are faster. Disable
thinking per-request with `chat_template_kwargs: {"enable_thinking": false}`.

## Notes

- 27B dense Gated-DeltaNet, 64 layers, `full_attention_interval` 4, head_dim 256.
  1199 tensors including 15 `mtp.*` — the MTP head is what makes speculative
  decoding available in both runtimes.
- The model also carries 333 vision tensors. Both configs run text-only.
- This is an uncensored/abliterated merge (refusals 4/100 against 99/100 for the
  base). It has no safety behaviour of its own; guardrails must live in the
  calling application.
