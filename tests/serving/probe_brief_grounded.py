#!/usr/bin/env python3
"""Synthetic concise-answer diagnostic, separate from original long-case gates.

Keeps the original tool schema, complete source fixture and grounding policy.
Changes only the caller's requested answer length. Structural/length checks do
not certify factual accuracy: retained final answers require source-based review.
"""
import argparse
import json
import os
from pathlib import Path
import time

from jsonschema import Draft202012Validator
from openai import OpenAI


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    args.output.mkdir(parents=True, exist_ok=False)
    here = Path(__file__).resolve().parent
    spec = json.loads((here / 'final_answer_schema.json').read_text())
    params = spec['parameters']
    fixture = json.loads((here / 'grounded_sources.json').read_text())
    policy = (here / 'source_grounding_policy.txt').read_text().strip()
    sources = ('Current-turn sources: source 1 is a personal health read with findings series:0 to series:5; '
               'source 2 is a literature read about sleep regularity and cardiometabolic risk.')
    messages = [{'role': 'system', 'content': sources + ' '
                 'Answer concisely in two to four evidence_review blocks, with at most one followup. '
                 'Use at most 150 words across claim text and followups. '
                 'Use text claims only. Select only blocks that add useful information; '
                 'do not repeat the same limitation.'},
                {'role': 'system', 'content': json.dumps(fixture, separators=(',', ':'))},
                {'role': 'system', 'content': policy},
                {'role': 'user', 'content': 'What should I prioritize, using both my data and the literature?'}]
    client = OpenAI(base_url=os.environ['MODEL_BASE_URL'], api_key=os.environ['MODEL_API_KEY'],
                    timeout=150, max_retries=0)
    results = []
    for repeat in range(args.repeats):
        for budget in ([None, 512] if repeat % 2 == 0 else [512, None]):
            name = f'{repeat + 1:02d}-' + ('default' if budget is None else str(budget))
            extra = {'top_k': 20}
            if budget is not None:
                extra['custom_params'] = {'thinking_budget': budget}
            body = dict(model='fable-distill', messages=messages, max_tokens=12000,
                        temperature=0.6, top_p=0.95, extra_body=extra,
                        tools=[{'type': 'function', 'function': {'name': spec['tool_name'],
                               'description': spec['tool_description'], 'parameters': params, 'strict': True}}],
                        tool_choice={'type': 'function', 'function': {'name': spec['tool_name']}},
                        parallel_tool_calls=False)
            # This fixed synthetic body contains neither client credentials nor URL.
            (args.output / f'{name}-request.json').write_text(json.dumps(body, indent=2))
            started = time.monotonic()
            result = {'name': name, 'thinking_budget': budget, 'passed': False}
            try:
                response = client.chat.completions.create(**body)
                result['request_seconds'] = time.monotonic() - started
                (args.output / f'{name}.json').write_text(response.model_dump_json(indent=2))
                choice = response.choices[0]
                calls = choice.message.tool_calls or []
                if choice.finish_reason != 'tool_calls' or len(calls) != 1 or calls[0].function.name != spec['tool_name']:
                    raise ValueError('Incomplete or incorrect named tool response')
                value = json.loads(calls[0].function.arguments)
                Draft202012Validator(params).validate(value)
                answer = value['answer']
                blocks = answer['evidence_review']
                followups = answer.get('followups', [])
                text = ' '.join(str(b['claim'].get('text', '')) for b in blocks) + ' ' + ' '.join(followups)
                result.update(blocks=len(blocks), followups=len(followups), claim_text_words=len(text.split()),
                              usage=response.usage.model_dump() if response.usage else None)
                result['passed'] = (2 <= len(blocks) <= 4 and len(followups) <= 1
                                    and all(b['claim']['type'] == 'text' for b in blocks)
                                    and len(text.split()) <= 150)
            except Exception as exc:
                result['error'] = type(exc).__name__
            results.append(result)
            (args.output / 'results.json').write_text(json.dumps(results, indent=2))
            print(json.dumps(result), flush=True)
    return int(any(not row['passed'] for row in results))


if __name__ == '__main__':
    raise SystemExit(main())
