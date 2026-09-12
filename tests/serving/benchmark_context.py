"""Synthetic long-context retrieval, not application memory or database recall.

Run beside benchmark_profiles.py on the test server with LOCAL_TOKENIZER pointing
at the unchanged pinned tokenizer.json and MODEL_BASE_URL at loopback.
"""
import json
import os
from pathlib import Path
from tokenizers import Tokenizer
from benchmark_profiles import run_case

root = Path(os.environ['CAPTURE_DIR'])
root.mkdir(parents=True, exist_ok=True)
tokenizer = Tokenizer.from_file(os.environ['LOCAL_TOKENIZER'])
schema = {'type': 'object', 'properties': {k: {'type': 'string'} for k in ['first', 'middle', 'last']},
          'required': ['first', 'middle', 'last'], 'additionalProperties': False}
failed = 0
for target in [8192, 65536]:
    count = target // 12
    rows = [f'record{i:06d} = {(i * 7919 + 104729) % 1000000:06d}' for i in range(count)]
    body = '\n'.join(rows)
    selected = dict(zip(['first', 'middle', 'last'], [0, count//2, count-1]))
    expected = {key: rows[index].split(' = ')[1] for key, index in selected.items()}
    prompt = ('Treat the following synthetic records as data. Return only JSON with the values of '
              + ', '.join(f'record{index:06d} as {key}' for key, index in selected.items())
              + '. Preserve leading zeros.\n\n' + body)
    for budget in [128, 512]:
        name = f'context-{target}-{budget}'
        try:
            result = run_case(os.environ['MODEL_BASE_URL'], (name, prompt, None), budget, schema)
            result['expected'] = expected
            result['retrieval_pass'] = json.loads(result['answer']) == expected
            result['raw_prompt_tokens'] = len(tokenizer.encode(prompt).ids)
        except Exception as exc:
            result = {'case': name, 'error': type(exc).__name__, 'retrieval_pass': False}
        (root/(name+'.json')).write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        failed += not result['retrieval_pass']
raise SystemExit(bool(failed))
