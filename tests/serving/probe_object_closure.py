"""Live original-schema regression: reject impossible objects, keep valid alternatives."""
import json
import os
from pathlib import Path
import time
from openai import OpenAI, BadRequestError
from jsonschema import Draft202012Validator


def main():
    output = Path(os.environ['CAPTURE_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    cases = [
        ('closed', {'allOf': [{'type': 'object', 'required': ['x']},
                              {'type': 'object', 'additionalProperties': False}]}, False),
        ('nullable', {'type': ['object', 'null'], 'required': ['x'], 'additionalProperties': False}, True),
        ('declared', {'type': 'object', 'required': ['x'], 'properties': {'x': {'type': 'integer'}}, 'additionalProperties': False}, True),
    ]
    failed = 0
    with OpenAI(base_url=os.environ['MODEL_BASE_URL'], api_key=os.environ.get('MODEL_API_KEY', 'local-test'), timeout=60, max_retries=0) as client:
        for name, schema, satisfiable in cases:
            start = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model='fable-distill', messages=[{'role': 'user', 'content': 'Return the JSON value {}.'}],
                    response_format={'type': 'json_schema', 'json_schema': {'name': 'object_closure', 'strict': True, 'schema': schema}},
                    max_tokens=512, temperature=0.6,
                    extra_body={'custom_params': {'thinking_budget': 32}},
                )
                value = json.loads(response.choices[0].message.content or '')
                valid = Draft202012Validator(schema).is_valid(value)
                passed = satisfiable and valid and response.choices[0].finish_reason == 'stop'
                result = {'case': name, 'http_status': 200, 'value': value, 'schema_valid': valid,
                          'finish': response.choices[0].finish_reason, 'passed': passed}
            except BadRequestError as exc:
                result = {'case': name, 'http_status': exc.status_code, 'passed': not satisfiable}
            except Exception as exc:
                result = {'case': name, 'error': type(exc).__name__, 'passed': False}
            result['elapsed'] = time.monotonic() - start
            (output / (name+'.json')).write_text(json.dumps(result, indent=2))
            print(json.dumps(result), flush=True)
            failed += not result['passed']
    return bool(failed)

if __name__ == '__main__':
    raise SystemExit(main())
