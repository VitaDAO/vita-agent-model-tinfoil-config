# vita-agent-model — usage guide

Confidential (TEE) inference endpoint serving **Qwen3.8-27B-Fable-Distill** in FP8 on a single
H200. Everything below is measured on this deployment, not estimated.

- **Model id:** `fable-distill`
- **Endpoint:** `https://vita-agent-model.vitality-now.containers.tinfoil.dev`
- **API:** OpenAI-compatible (`/v1/chat/completions`, `/v1/completions`, `/v1/models`)
- **Auth:** none — authentication is attestation-based, via the verified proxy
- **Context:** 262,144 tokens · **Concurrency:** 16 slots · **Live config:** tag `v0.6.1`

---

## 1. Connect

The endpoint is reached through Tinfoil's verified proxy, which checks the enclave's attestation
before forwarding anything:

```bash
tinfoil container connect vita-agent-model -p 3301
# -> http://localhost:3301/v1
```

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:3301/v1", api_key="not-used")
```

Health check: `curl -s http://localhost:3301/health` → `{"status":"ok"}`

---

## 2. The one thing you must get right

**`reasoning_effort` defaults to `xhigh`, and thinking tokens count against `max_tokens`.**
A default call with a small budget spends the whole budget thinking and returns an **empty**
`content`. This is the single most common way to "break" this endpoint.

Always set the mode explicitly:

```python
# fastest — no thinking at all
extra_body={"chat_template_kwargs": {"enable_thinking": False}}

# thinking, with visible reasoning in message.reasoning_content
extra_body={"chat_template_kwargs": {"reasoning_effort": "medium"}}   # or "low" / "xhigh"
```

---

## 3. Which mode to use

Measured over 12 reasoning tasks with objectively checkable answers (Bayes/serial testing, NNT,
Simpson's paradox, 4-door Monty Hall, renal dose adjustment, knights-and-knaves) — **all modes
scored 100%**, so thinking buys latency, not accuracy, on well-posed problems.

| Use case | Setting | `max_tokens` | Latency | Notes |
|---|---|---|---|---|
| Reasoning, math, clinical calc, chat | `enable_thinking: false` | 1000 | **0.8–1.7 s** | 100% accuracy; the default choice |
| Auditable step-by-step work | `reasoning_effort: "medium"` | 3000 | ~5 s | exposes `reasoning_content` |
| Hard literature/knowledge recall | `reasoning_effort: "xhigh"` | **8000+** | ~20 s | best factual accuracy; starves below ~4k |
| — | `reasoning_effort: "low"` | 3000 | ~6.5 s | no better than medium, sometimes longer |

`xhigh` scales its thinking to difficulty: 29 tokens for "17 × 23", but 3,638 tokens
(12k characters of reasoning) for a hard literature question. Budget accordingly.

---

## 4. Sampling

Author's recommended values, which are also what the speed numbers below were measured with:

```python
temperature=1.0, top_p=0.95, top_k=20        # thinking mode (creative)
temperature=0.6, top_p=0.95, top_k=20        # general use
temperature=0.3                              # analysis, extraction, calculation
```

**Never set `presence_penalty` or `repetition_penalty` above 0** — penalties wreck the
speculative-decoding draft head and cost ~10% throughput for no quality gain.
Lower temperature is *faster* here (see below).

---

## 5. Speed you can expect

**Average 176 tok/s** (median 178) across 22 measurements; range 128–212 single-stream.

| Workload | Speed |
|---|---|
| Reasoning / calculation at temp 0.3 | **195–212 tok/s** |
| Long documents (34k–39k tokens) | **176–208 tok/s** |
| General Q&A at temp 0.6 | 150–190 tok/s |
| Creative generation at temp 1.0 | 128–140 tok/s |
| 8 concurrent requests | **640 tok/s aggregate** |

Time-to-first-token: **0.26–0.29 s** short prompts, **3.4 s** for a 39k-token prompt,
dropping to **0.67 s** on a repeat with the same prefix (radix prefix cache).

Two counterintuitive but reproducible effects, both from speculative decoding:
**lower temperature is faster**, and **longer context is faster** (draft acceptance rises when
the model has more context to predict from). Your worst case is short, high-temperature creative
work; long-context analysis runs at the top of the range.

---

## 6. What it is good and bad at

**Excellent — reasoning over material you provide.** Given a 39,244-token document with one fact
buried in the middle, it retrieved the fact, quoted it verbatim, and cited the right section, at
208 tok/s. Every quantitative reasoning task tested passed, including ones designed to trap
(base-rate neglect, confidence intervals crossing 1, sequential testing).

**Unreliable — recalling facts from memory.** Asked the same biomedical literature question in
different modes, it produced mutually contradictory answers and **fabricated plausible citations**
(attributing a compound to "Harrison et al., Nature 2009", which is a different paper). It does
hedge honestly ("recalling from memory, may be off"), but do not use it as a factual source.

**Implication: build retrieval-augmented, not recall-based.** Feed it sources and it is strong;
ask it to remember and it confabulates. This is also the cheapest path to accuracy, since
long-context work is its fastest mode.

Also note: it identifies itself as "Qwen" — the distillation changed how it reasons and writes,
not what the base model believes it is. Override with a system prompt if that matters.

---

## 7. Examples

```python
# Fast structured extraction
r = client.chat.completions.create(
    model="fable-distill",
    messages=[{"role": "user", "content": f"{document}\n\nExtract all endpoints as JSON."}],
    max_tokens=1000, temperature=0.3,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
print(r.choices[0].message.content)

# Reasoning with visible chain of thought
r = client.chat.completions.create(
    model="fable-distill",
    messages=[{"role": "user", "content": "Assess the statistical validity of this trial design..."}],
    max_tokens=3000, temperature=0.6,
    extra_body={"chat_template_kwargs": {"reasoning_effort": "medium"}},
)
print(r.choices[0].message.reasoning_content)   # the thinking
print(r.choices[0].message.content)             # the answer
```

Tool calling is enabled (`--tool-call-parser qwen3_coder`); pass `tools=[...]` as usual.
Streaming works normally, and `reasoning_content` streams before `content`.

### 7b. Vision — enabled, no flag required

The model is image-text-to-text and the vision tower is served. Pass images as base64 data URLs
(the enclave has no internet, so **remote image URLs will not work**):

```python
import base64
b64 = base64.b64encode(open("figure.png", "rb").read()).decode()
r = client.chat.completions.create(
    model="fable-distill",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "Read this chart and compute the absolute risk reduction."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}],
    max_tokens=900, temperature=0.3,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
```

Measured: a 640×400 test image cost 240 image tokens and was described correctly down to shape
positions, outlines and embedded text, in 1.6 s. A labelled bar chart (384 image tokens) had all
four values read exactly, plus a correct downstream ARR and NNT calculation, in **1.2 s**.
Images bill as prompt tokens — check `usage.prompt_tokens_details.image_tokens`.

---

## 8. Operating notes

- **Vision is ON** — no flag needed, see section 7b.
- **Restarts cost ~20 minutes** (dm-verity verification of the 28.8 GiB model artifact, weight
  load, CUDA-graph capture). It is not a service you bounce casually.
- **Rollback:** `tinfoil container start vita-agent-model --tag v0.6.1` restores this exact
  configuration; `tinfoil-config.nextn.yml` is the same config kept in-repo for reference.
- **Metrics:** `/metrics` exposes `sglang:spec_accept_length`, but it resets every 40 decode
  iterations — read `meta_info.spec_accept_length` per request instead for anything meaningful.
- **Model provenance:** FP8 quantization was produced in-house from the BF16 weights using Qwen's
  official recipe for this architecture (block-wise e4m3, 128×128, 407 tensors quantized, all
  Gated-DeltaNet state tensors and norms kept BF16). Audited at 2.65% relative error, uniform
  across every tensor, no outliers. Source repo: `alexdobrin/Qwen3.8-27B-Fable-Distill-FP8` (private).

See `speed_experiments.md` for the full experiment log behind these numbers.
