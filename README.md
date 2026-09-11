# vita-agent-model

Tinfoil configuration and serving-image source for the `fable-distill` endpoint.
The release configuration in `tinfoil-config.yml` serves **Fable Distill FP8 with
DFlash2**, using eight draft tokens on one confidential H200. It uses SGLang;
the alternative llama.cpp and NEXTN configuration files are historical examples,
not the active release configuration.

## Model and runtime identity

- Target: `alexdobrin/Qwen3.8-27B-Fable-Distill-FP8@dad2544d418ffb797af211fb49e4dd770af8e33f`
- Draft: `incoai/Qwen3.8-27B-DFlash2@dedf8df68adfb1afeaf7b7480c0a0243108177b4`
- Serving image: `ghcr.io/vitadao/vita-agent-model-sglang@sha256:47403a0af08f629a55fd65695bb250f378e9938c358b795ae50c1c38ad9cfe08`
- Image source: `9ebdc612db3fd351457dbe96991bf9e331f31ace`
- API model ID: `fable-distill`; context: 262,144 tokens; concurrency: 16 slots.
- Host allocation: eight CPUs, one H200, 128 GiB RAM; confidential computing on.

The image corrects the JSON-schema compiler and supplies the caller's original
JSON response contract to the model renderer. The launch configuration enables
native strict thinking with a default 4,096-token reasoning limit. This keeps
room for the final answer when callers provide an adequate total token budget;
it does not change model weights or repair generated output. See
[the serving contract and release evidence](docs/schema-compiler.md).

## Verification and known limitations

This exact image/configuration was exercised as test release `v0.5.95` on
September 11, 2026. All 30 original synthetic outputs completed and conformed to
the contract; 28/30 also met the probe's requested-answer criteria. Two named-tool
answers returned acknowledgments instead of the requested substantive answer.
The matched five-pass benchmark median was 127.5 tokens/sec versus 162.2 baseline
(21.4% slower), so the original 10% speed-regression target was not met.
The principal directed production promotion after these results were disclosed.
This is not a clean quality/performance pass. Streaming, concurrent contract
isolation, and default/per-request reasoning-limit checks passed.

The generic strict-thinking path adds grammar processing to ordinary responses;
its performance cost remains a follow-up. Application-level factual accuracy,
recall, and the application's 20-prompt battery are separate acceptance work.

## Deploying and connecting

Tags matching `v*.*.*` build and attest a configuration release. Tag publication
does not select a running version. Use the approved exact tag with the existing
`vita-agent-model` container, then verify its live attestation and health.
Check the live version with `tinfoil container get vita-agent-model`; a repository
commit or test release alone is not production deployment proof.

```sh
tinfoil container connect vita-agent-model -b 127.0.0.1 -p 3301
python3 bench_enclave.py --base-url http://127.0.0.1:3301/v1 --model fable-distill --passes 5 --max-tokens 2000
```

The proxy verifies the enclave's attestation before forwarding requests. Keep
certificate and attestation verification enabled. See [USAGE.md](USAGE.md) for
request-level reasoning budgets and the distinction from older measurements.

The old `v0.10.0` release is retained for recovery, but must not be automatically
restarted against the principal's explicit direction to replace it. Recovery
must use a recorded, authorized action for the intended production container.
