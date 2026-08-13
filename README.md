# Phase 0A — Fireworks cache proof

This project sends two streaming requests with an identical 2,000-word incident
prefix and cache key, but different final questions. It prints prompt tokens,
cached tokens, and client/server time to first token for both calls.

The script reads `FIREWORKS_API_KEY` from the shell or the existing local `.env`.
It never displays or modifies `.env` and uses only Python's standard library.

Run the local validation first:

```bash
python3 phase0_cache_proof.py --dry-run
```

Then run the live proof; if the key is not in the shell or `.env`, the script
prompts for it securely and does not save it:

```bash
python3 phase0_cache_proof.py
```

To retry immediately with another current Fireworks serverless model:

```bash
python3 phase0_cache_proof.py --model "accounts/fireworks/models/MODEL_ID"
```

Gate 1 passes when the second call reports cached tokens covering most of its
prompt. A faster second-call TTFT is useful supporting evidence, but can vary on
shared serverless infrastructure.
