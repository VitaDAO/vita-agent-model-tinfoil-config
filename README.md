# vita-agent-model

Tinfoil configuration and serving-image source for the `fable-distill` endpoint.
The release configuration in `tinfoil-config.yml` serves **Fable Distill FP8 with
DFlash2**, using eight draft tokens on one confidential H200. It uses SGLang;
the alternative llama.cpp and NEXTN configuration files are historical examples,
not the active release configuration.

## Model and runtime identity

- Target: `alexdobrin/Qwen3.8-27B-Fable-Distill-FP8@dad2544d418ffb797af211fb49e4dd770af8e33f`
- Draft: `incoai/Qwen3.8-27B-DFlash2@dedf8df68adfb1afeaf7b7480c0a0243108177b4`
- Rollback release `v0.10.1` (verified live before this release; attested digest `8ff0ca151438710252055cb5a7b8b3cd242b6749564006fb09143c9fc2704788`): serving image `ghcr.io/vitadao/vita-agent-model-sglang@sha256:47403a0af08f629a55fd65695bb250f378e9938c358b795ae50c1c38ad9cfe08`, built from `9ebdc612db3fd351457dbe96991bf9e331f31ace` with compiler `0.2.1+vita3`.
- Configured release `v0.10.2` (deployment evidence is tracked in [issue #3](https://github.com/VitaDAO/vita-agent-model-tinfoil-config/issues/3)): serving image `ghcr.io/vitadao/vita-agent-model-sglang@sha256:2fd6c1db75a33fe55a491e09930a87118aa39cc402b67cfe6281028c88d6c719`, built from `879a16a3fced261856c08cbf5be0dc57daf9dfa2` with compiler `0.2.1+vita4`. This is the digest this release pins in `tinfoil-config.yml`; `v0.10.1` remains the rollback target.
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

Retain `v0.10.1` as the recovery target for `v0.10.2`. Rollback must follow the
recorded release recovery plan for the intended production container; the older
`v0.10.0` release is not the rollback target.
