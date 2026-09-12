"""Negative cases for the named-tool acceptance boundary (no inference call)."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator, ValidationError

# The command-line probe executes its selected cases on import. Load only its
# pure boundary function; these tests never initialize a provider client.
source = ast.parse(Path(__file__).with_name('probe_constrained.py').read_text())
function = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'parse_tool_arguments')
namespace = {'json': json, 'Draft202012Validator': Draft202012Validator}
exec(compile(ast.Module(body=[function], type_ignores=[]), '<probe boundary>', 'exec'), namespace)
parse = namespace['parse_tool_arguments']
SCHEMA = {'type': 'object', 'properties': {'answer': {'type': 'string'}},
          'required': ['answer'], 'additionalProperties': False}
TOOLS = [{'function': {'name': 'submit_final_answer', 'parameters': SCHEMA}}]
CHOICE = {'function': {'name': 'submit_final_answer'}}


def tool(args, name='submit_final_answer'):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(args)))


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


if __name__ == '__main__':
    unittest.main()
