# Prompting guide — `fable-distill`

How to get good output from this endpoint. Every claim here was tested against the live
deployment; see `USAGE.md` for connection details and `speed_experiments.md` for the speed work.

The model is **Qwen3.8-27B-Fable-Distill**: a Fable-5-distilled fine-tune of Qwen3.8-27B, served
in FP8 with 256k context and vision enabled.

---

## The 60-second version

```python
extra_body={"chat_template_kwargs": {"enable_thinking": False}}   # set this, or you may get ""
temperature=0.3        # analysis/extraction     0.6 general     1.0 creative
# no presence_penalty, no repetition_penalty — they cost ~10% speed and buy nothing
```

Three rules that matter more than any phrasing trick:

1. **Always set the thinking mode explicitly.** The default is `xhigh`, thinking tokens count
   against `max_tokens`, and an under-budgeted call returns an **empty** answer.
2. **Give it the source material.** It reasons superbly over text you supply and confabulates
   when recalling from memory. This is the single biggest quality lever.
3. **Tell it that "I don't know" is allowed.** Tested: with that instruction it declines cleanly
   instead of inventing. Without it, it invents citations.

---

## 1. Choosing a thinking mode

| Task | Setting | `max_tokens` | Measured latency |
|---|---|---|---|
| Extraction, classification, chat, calculation | `enable_thinking: false` | 500–1000 | 0.4–1.7 s |
| Analysis you want to audit | `reasoning_effort: "medium"` | 3000 | ~5 s |
| Hard recall / genuinely novel problems | `reasoning_effort: "xhigh"` | **8000+** | ~20 s |

Across 12 reasoning tasks with verifiable answers (Bayes with sequential testing, NNT, Simpson's
paradox, 4-door Monty Hall, renal dose adjustment, knights-and-knaves) **every mode scored 100%**.
Thinking did not buy accuracy on well-posed problems — only latency. It *did* help on hard
literature recall, where `xhigh` alone got the answer right.

`reasoning_effort: "low"` is not reliably shorter than `medium` — it once produced a 4,000-token
answer. Prefer `medium` when you want thinking at all.

When thinking is on, the chain of thought arrives separately:

```python
msg = r.choices[0].message
msg.reasoning_content   # the thinking — log it, don't show users
msg.content             # the answer
```

---

## 2. Sampling

| Purpose | Settings |
|---|---|
| Extraction, calculation, structured output | `temperature=0.2–0.3, top_p=0.95` |
| General Q&A, analysis | `temperature=0.6, top_p=0.95, top_k=20` |
| Creative / long-form writing | `temperature=1.0, top_p=0.95, top_k=20` |

**Never set `presence_penalty` or `repetition_penalty`.** They degrade the speculative-decoding
draft head — measured ~10% slower with no quality gain. Lower temperature is also *faster* here
(higher draft acceptance), so analysis workloads run at 195–210 tok/s versus ~130 at temp 1.0.

---

## 3. The patterns that matter

### 3a. Ground it — this is the whole game

Its retrieval and reasoning over supplied text is excellent: given a 39,244-token document with a
single fact buried in the middle, it found the fact, quoted it verbatim, and named the right
section — at 208 tok/s. Its parametric recall is not trustworthy: asked the same literature
question in different modes it gave contradictory answers and invented plausible citations.

```
[full document text]

---
Using ONLY the document above, answer: <question>.
Quote the sentence you relied on. If the document does not contain the answer, say so.
```

Put the **document first and the question last**. Long context costs little: 39k tokens prefills
in 3.4 s, and drops to 0.67 s on a repeat with the same prefix thanks to the prefix cache — so
keep a stable document prefix across turns and vary only the tail.

### 3b. Licence it to decline

Tested with a fabricated trial name and compound. With this instruction it answered:
*"I don't know… these don't appear to correspond to any trial or compound I'm aware of, and the
names look like placeholders. I won't guess."*

```
If you do not know, say so plainly and do not guess.
```

Add it to any prompt that touches facts, numbers, citations, or dosing. Without it, the model
tends to produce confident, well-formatted, wrong answers — the most dangerous failure mode for
biomedical work.

### 3c. Structured output — use the schema, not a plea

JSON schema enforcement works (tested, 2.1 s, valid object first try). Prefer it over asking
nicely for JSON.

```python
r = client.chat.completions.create(
    model="fable-distill",
    messages=[{"role": "user", "content": f"Extract the fields from: {text}"}],
    max_tokens=400, temperature=0.2,
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    response_format={"type": "json_schema", "json_schema": {"name": "finding", "schema": {
        "type": "object",
        "properties": {"compound": {"type": "string"}, "significant": {"type": "boolean"},
                       "sex": {"type": "string"}, "percent": {"type": "number"}},
        "required": ["compound", "significant", "sex", "percent"],
        "additionalProperties": False}}},
)
```

### 3d. System prompts hold

The base model self-identifies as "Qwen" — the distillation changed how it reasons and writes,
not what it believes it is. A system prompt overrides this reliably (tested):

```python
{"role": "system", "content":
 "You are VitaBot, VitaDAO's longevity research assistant. Never mention Qwen or Alibaba. "
 "Cite only sources provided in the conversation. If you do not know, say so."}
```

### 3e. Vision

Pass base64 data URLs — the enclave has no internet, so remote image URLs fail. It read a
labelled bar chart exactly and computed the absolute risk reduction and NNT from it in 1.2 s.

```
Read this figure. List each series with its exact value, then <calculation>.
If a value is ambiguous or unlabelled, say so rather than estimating.
```

That last sentence matters: it will otherwise interpolate unlabelled values.

---

## 4. Anti-patterns

| Don't | Why |
|---|---|
| Leave `reasoning_effort` at its default with a small `max_tokens` | Empty answers — thinking eats the budget |
| Ask for citations, p-values or doses from memory | Fabricates plausible references |
| Use `presence_penalty` / `repetition_penalty` | ~10% slower, no quality benefit |
| Put the question before a long document | Question-last measurably improves retrieval |
| Ask for "a summary" with no length or audience | Verbose by default; specify both |
| Show `reasoning_content` to end users | It is scratch work, often rambling |
| Rely on remote image URLs | No internet in the enclave |

---

## 5. Templates

**Literature/document analysis**
```
[paper text]

---
Using only the text above:
1. What was the primary endpoint and was it met?
2. Sample size per arm, and was the study powered for this endpoint?
3. The three biggest threats to validity, ranked.
Quote the sentence supporting each answer. If something is not stated, write "not reported".
```
*non-thinking, temp 0.3, max_tokens 1500*

**Critical appraisal (reasoning you want to audit)**
```
A study reports: <claims>.
Assess whether the conclusion follows from the data. Address confounding, multiplicity,
selection effects and effect-size plausibility. State what additional data would change your view.
```
*`reasoning_effort: "medium"`, temp 0.6, max_tokens 3000 — read `reasoning_content` for the audit trail*

**Structured extraction at scale**
```
Extract every reported outcome from the text below.
Return one object per outcome. Use null for anything not stated — never infer.
```
*non-thinking, temp 0.2, `response_format` json_schema, max_tokens 1000*

**Figure reading**
```
Read this figure. Give each series and its exact value, then compute <metric>.
If a value is unlabelled, say so rather than estimating.
```
*non-thinking, temp 0.2, base64 image, max_tokens 900*

---

## 6. Speed expectations by pattern

| Pattern | Speed |
|---|---|
| Extraction / calculation (temp 0.2–0.3) | 195–212 tok/s |
| Document analysis (34k–39k context) | 176–208 tok/s |
| General Q&A (temp 0.6) | 150–190 tok/s |
| Creative writing (temp 1.0) | 128–140 tok/s |
| 8 concurrent requests | 640 tok/s aggregate |

Average across 22 measurements: **176 tok/s**. Time-to-first-token is 0.26–0.29 s for short
prompts, 3.4 s for a 39k-token prompt, 0.67 s on a cached prefix.
