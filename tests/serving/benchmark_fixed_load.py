"""Fixed-token synthetic serving benchmark, not an answer-quality evaluation.

All requests must emit exactly the configured completion-token count. EOS is
ignored deliberately so different answer lengths cannot inflate throughput.
Use identical flags, model revisions, client placement and concurrency for A/B.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import time

from openai import OpenAI


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--requests', type=int, default=8)
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--tokens', type=int, default=2048)
    parser.add_argument('--batches', type=int, default=3)
    args = parser.parse_args()
    if min(args.requests, args.concurrency, args.tokens, args.batches) < 1:
        parser.error('all counts must be positive')
    args.output.mkdir(parents=True, exist_ok=True)

    def run(index):
        start = time.monotonic()
        with OpenAI(base_url=os.environ['MODEL_BASE_URL'],
                    api_key=os.environ.get('MODEL_API_KEY', 'local-test'),
                    timeout=180, max_retries=0) as client:
            response = client.chat.completions.create(
                model='fable-distill',
                messages=[{'role': 'user', 'content':
                    f'Synthetic benchmark item {index}: write a detailed technical essay '
                    'about how a computer executes a program, from source to machine code. '
                    'Explain each stage with examples.'}],
                temperature=0.6, top_p=0.95, max_tokens=args.tokens,
                extra_body={'top_k': 20, 'ignore_eos': True,
                            'custom_params': {'thinking_budget': 128}},
            )
        usage = response.usage.model_dump() if response.usage else {}
        finish = response.choices[0].finish_reason if response.choices else None
        if usage.get('completion_tokens') != args.tokens or finish != 'length':
            raise RuntimeError(f'Unequal workload: tokens={usage.get("completion_tokens")}, finish={finish}')
        return {'index': index, 'elapsed': time.monotonic() - start,
                'usage': usage, 'finish': finish}

    batches = []
    for batch in range(args.batches):
        start = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            rows = list(pool.map(run, range(args.requests)))
        elapsed = time.monotonic() - start
        result = {'batch': batch, 'elapsed': elapsed,
                  'completion_tokens': args.requests * args.tokens,
                  'tokens_per_second': args.requests * args.tokens / elapsed,
                  'requests': rows, 'concurrency': args.concurrency}
        (args.output / f'batch-{batch}.json').write_text(json.dumps(result, indent=2))
        batches.append(result)
        print(json.dumps({k: v for k, v in result.items() if k != 'requests'}), flush=True)
    (args.output / 'summary.json').write_text(json.dumps(batches, indent=2))


if __name__ == '__main__':
    main()
