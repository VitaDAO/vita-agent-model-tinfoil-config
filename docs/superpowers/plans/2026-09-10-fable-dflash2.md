# Fable DFlash2 Implementation Plan

Goal: Run the existing Fable Distill FP8 model with DFlash2, concurrent inference, and Tinfoil attestation on the existing production H200.

Architecture: Keep the current model enclave and OpenAI-compatible alias. Add the pinned draft MPK and replace native MTP with DFlash2 in a digest-pinned SGLang image. Preserve confidential mode and the attested shim.

Tech stack: Tinfoil cvm0.7.5, SGLang nightly db272201a, H200, FP8 target and draft.

Spec: Issue #1 and the user's active request. This standalone configuration repository has only main; no application repository changes.

- [x] Verify live v0.6.1, exact target weights, wrapped draft, benchmark evidence, and release workflow.
- [x] Preserve target MPK, model alias, loopback, shim, context262144, max-running16, resources8CPU/128GiB/1GPU.
- [x] Configure DFLASH with draft block8 and pinned draft path; migrate renamed CLI flags.
- [x] Parse YAML, compare invariants with base, check all flags against captured nightly help, inspect diff.
- [ ] Obtain exact-revision lifecycle authorization after the candidate is reviewable, including omitted separate-enclave preflight and rollback.
- [ ] Merge candidate PR, verify resulting tree, recheck unused candidate tag v0.10.0 in this repository, tag the verified tree.
- [ ] Wait for existing Build & Attest workflow; verify success and release attestation assets before any relaunch.
- [ ] Relaunch only vita-agent-model on control.inf6.tinfoil.sh using the candidate tag, debug=false, existing variables/secrets/SSH unchanged and autoupdate=false.
- [ ] Verify attested health and /v1/models (fable-distill, max262144), then synthetic temperature0 extraction/tool/grounding and long-input tests, and eight simultaneous requests. Query metrics for draft acceptance and memory.
- [ ] If attestation/health or critical tests fail, restore v0.6.1 and verify attested health/model identity. Do not weaken privacy or enable debug to recover.
- [ ] Record actual deployed tag, checks, concurrency results, remaining limits, and production agent health. Close only after intended environment is verified.

## Privacy and parallelism

Do not add SSH keys, debug mode, host exposure, prompt logging, secrets, or alternate unverified model routes. Keep HF and Transformers offline; both model packages are verified MPKs. Existing shim paths and configuration repository remain the attestation anchor. SGLang overlap is enabled by default; V2 is intrinsic in this nightly. Max-running16 is a capacity setting, not proof of16-request stability; benchmark8, then assess larger concurrency separately.

## Material limitations

RunPod tests covered32K context, temperature0.6,12 objective checks per method and one concurrency8 batch. Full-context greedy requests and offline enclave startup of the new image remain untested. Candidate image may need bundled kernel/cache dependencies if offline startup fails; treat this as a failure requiring recovery and a new candidate, not permission to disable offline settings. Same-H200 switch can interrupt Fable while loading; production agent stays separate. No precise downtime estimate is established.

## Bound rollout / recovery packet

Actor: Codex directed by @DobrinAlexandru. Target: production vita-agent-model (5d4e1789-111f-493f-9182-b30b5a3ead0c), vita-agent-model.vitality-now.containers.tinfoil.dev, config VitaDAO/vita-agent-model-tinfoil-config. Current/rollback tag:v0.6.1 (0d6ee332fe01bd404e3f60f8b9ef6ef7afe093fa). Candidate tag:v0.10.0, to bind to the exact reviewed PR tree; recheck availability immediately before tagging. Image:lmsysorg/sglang@sha256:64e4914593690fbba806f19ac8b2237f78815e9046dd2fc874f630129f281607. Fable revision:dad2544d418ffb797af211fb49e4dd770af8e33f. DFlash2 revision:dedf8df68adfb1afeaf7b7480c0a0243108177b4. No migrations or secret changes. Authority expires after this rollout/rollback attempt or24hours. Exact candidate commit must be recorded in the issue/PR before authorization and execution.
