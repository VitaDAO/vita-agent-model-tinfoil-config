"""No-inference checks for the probe's envelope boundary and its long-case audit."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator, ValidationError

# The command-line probe executes its selected cases on import. Load only its
# pure helpers and constants; these tests never initialize a provider client.
WANTED_FUNCTIONS = {'parse_tool_arguments', 'judge', 'long_obligation_failures', 'redact'}
WANTED_ASSIGNMENTS = {'LONG_OBLIGATIONS', 'SECRET_FIELD_NAMES'}
source = ast.parse(Path(__file__).with_name('probe_constrained.py').read_text())
body = [node for node in source.body
        if (isinstance(node, ast.FunctionDef) and node.name in WANTED_FUNCTIONS)
        or (isinstance(node, ast.Assign)
            and any(getattr(target, 'id', None) in WANTED_ASSIGNMENTS for target in node.targets))]
namespace = {'json': json, 'Draft202012Validator': Draft202012Validator}
exec(compile(ast.Module(body=body, type_ignores=[]), '<probe boundary>', 'exec'), namespace)
parse = namespace['parse_tool_arguments']
judge = namespace['judge']
long_obligation_failures = namespace['long_obligation_failures']
redact = namespace['redact']
LONG_OBLIGATIONS = namespace['LONG_OBLIGATIONS']
SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}},
          'required': ['answer'], 'additionalProperties': False}
TOOLS = [{'function': {'name': 'submit_final_answer', 'parameters': SCHEMA}}]
CHOICE = {'function': {'name': 'submit_final_answer'}}

ANSWER_SCHEMA = json.loads(Path(__file__).with_name('final_answer_schema.json').read_text())
ANSWER = ANSWER_SCHEMA['parameters']['properties']['answer']


def tool(args, name='submit_final_answer'):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def block(basis='observed_record', source_ids=(1,), text='Synthetic claim.'):
    return {'support': 'Synthetic support.', 'basis': basis, 'source_ids': list(source_ids),
            'passage_ids': [], 'claim': {'type': 'text', 'text': text}}


def long_answer(block_count=6, clinical=2, followups=2):
    """A schema-valid long answer whose prose obligations are set independently."""
    blocks = [block('clinical_interpretation', (1, 2)) for _ in range(clinical)]
    blocks += [block() for _ in range(block_count - clinical)]
    answer = {'evidence_review': blocks, 'excluded_sources': []}
    if followups is not None:
        answer['followups'] = [f'Synthetic follow-up question number {i}?' for i in range(followups)]
    return answer


ONE_BLOCK = {'evidence_review': [block('clinical_interpretation', (1, 2))]}


class EnvelopeTest(unittest.TestCase):
    def test_exact_named_tool(self):
        self.assertEqual(parse(SimpleNamespace(tool_calls=[tool({'answer': 'ok'})]), TOOLS, CHOICE), {'answer': 'ok'})

    def test_missing_duplicate_and_wrong_name(self):
        for calls in [[], [tool({'answer': 'ok'})] * 2, [tool({'answer': 'ok'}, 'other')]]:
            with self.subTest(calls=len(calls)), self.assertRaises(ValueError):
                parse(SimpleNamespace(tool_calls=calls), TOOLS, CHOICE)

    def test_full_envelope_rejects_missing_or_forbidden_arguments(self):
        for args in [{}, {'answer': 'ok', 'unexpected': True}]:
            with self.subTest(args=args), self.assertRaises(ValidationError):
                parse(SimpleNamespace(tool_calls=[tool(args)]), TOOLS, CHOICE)

    def test_no_tool_is_permitted_for_response_format(self):
        self.assertIsNone(parse(SimpleNamespace(tool_calls=None), [], None))


class LongObligationTest(unittest.TestCase):
    def test_one_block_is_schema_valid_and_passes_the_short_case_metric(self):
        self.assertTrue(Draft202012Validator(ANSWER).is_valid(ONE_BLOCK))
        self.assertEqual(judge(ONE_BLOCK, ANSWER, 1), (True, 'ok'))

    def test_one_block_fails_both_long_case_metrics(self):
        self.assertEqual(judge(ONE_BLOCK, ANSWER, 6), (False, 'only 1 blocks'))
        ok, why = judge(ONE_BLOCK, ANSWER, 6, LONG_OBLIGATIONS)
        self.assertFalse(ok)
        self.assertIn('1 evidence_review blocks, expected 6-8', why)
        self.assertIn('1 clinical_interpretation blocks, expected 2', why)
        self.assertIn('0 followups, expected 2', why)

    def test_complete_long_answer_passes_both_metrics(self):
        answer = long_answer()
        self.assertTrue(Draft202012Validator(ANSWER).is_valid(answer))
        self.assertEqual(judge(answer, ANSWER, 6), (True, 'ok'))
        self.assertEqual(judge(answer, ANSWER, 6, LONG_OBLIGATIONS), (True, 'ok'))

    def test_prose_obligation_negatives(self):
        cases = {
            '5 evidence_review blocks, expected 6-8': long_answer(block_count=5),
            '9 evidence_review blocks, expected 6-8': long_answer(block_count=9),
            '1 clinical_interpretation blocks, expected 2': long_answer(clinical=1),
            '3 clinical_interpretation blocks, expected 2': long_answer(clinical=3),
            '1 followups, expected 2': long_answer(followups=1),
            '3 followups, expected 2': long_answer(followups=3),
            '0 followups, expected 2': long_answer(followups=0),
        }
        for expected, answer in cases.items():
            with self.subTest(expected=expected):
                # Every case stays schema-valid: these are prose, not grammar, failures.
                self.assertTrue(Draft202012Validator(ANSWER).is_valid(answer))
                ok, why = judge(answer, ANSWER, 6, LONG_OBLIGATIONS)
                self.assertFalse(ok)
                self.assertIn(expected, why)

    def test_clinical_block_without_both_sources_is_reported(self):
        answer = long_answer(clinical=1)
        answer['evidence_review'][0]['source_ids'] = [1]
        failures = long_obligation_failures(answer, LONG_OBLIGATIONS)
        self.assertIn('clinical_interpretation block 1 missing source_ids [2]', failures)

    def test_schema_validity_is_decided_before_prose_obligations(self):
        invalid = {'evidence_review': [{'basis': 'clinical_interpretation'}]}
        ok, why = judge(invalid, ANSWER, 6, LONG_OBLIGATIONS)
        self.assertFalse(ok)
        self.assertIn('schema errors', why)
        self.assertNotIn('expected 6-8', why)

    def test_legacy_sentinels_are_unchanged(self):
        self.assertEqual(judge(None, ANSWER), (False, 'no JSON content'))
        self.assertEqual(judge('text', ANSWER), (False, 'value returned as a raw string (grammar not applied)'))


class RequestCaptureTest(unittest.TestCase):
    def test_credentials_are_redacted_and_settings_survive(self):
        body = {'model': 'fable-distill', 'max_tokens': 7000,
                'api_key': 'sk-not-written', 'Authorization': 'Bearer not-written',
                'messages': [{'role': 'user', 'content': 'synthetic'}],
                'extra_body': {'custom_params': {'thinking_budget': 32}, 'token': 'not-written'}}
        captured = redact(body)
        self.assertEqual(captured['max_tokens'], 7000)
        self.assertEqual(captured['messages'], body['messages'])
        self.assertEqual(captured['extra_body']['custom_params'], {'thinking_budget': 32})
        for path in [('api_key',), ('Authorization',), ('extra_body', 'token')]:
            value = captured
            for key in path:
                value = value[key]
            self.assertEqual(value, '<redacted>')


if __name__ == '__main__':
    unittest.main()
