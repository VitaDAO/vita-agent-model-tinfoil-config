"""Exercise the pinned SGLang rendering methods without a GPU/model import.

Only infrastructure at the renderer boundary is replaced: a recording tokenizer,
message DTOs and multimodal conversion. The production methods execute unchanged.
The image build runs this against its installed source, not a copied test method.
"""
import ast
import copy
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


SOURCE = Path(sys.argv.pop(1))
tree = ast.parse(SOURCE.read_text())
owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OpenAIServingChat")
methods = [n for n in owner.body if isinstance(n, ast.FunctionDef) and n.name in {
    "_apply_jinja_template", "_handle_last_assistant_message",
}]
namespace = {
    "copy": copy, "json": json, "logger": logging.getLogger(__name__),
    "envs": SimpleNamespace(SGLANG_DEFAULT_THINKING=SimpleNamespace(get=lambda: True)),
    "ThinkingMode": SimpleNamespace(THINKING="thinking", CHAT="chat"),
    "normalize_assistant_tool_call_arguments": lambda *a, **kw: None,
    "normalize_tool_content": lambda role, content: content,
    "process_content_for_template_format": lambda message, *a, **kw: message,
    "MessageProcessingResult": SimpleNamespace,
    "_CHAT_TEMPLATE_CLIENT_ERRORS": (ValueError, TypeError),
}
module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *methods], type_ignores=[])
exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)


class Message(dict):
    def model_dump(self):
        return copy.deepcopy(dict(self))


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        self.messages = copy.deepcopy(messages)
        self.kwargs = kwargs
        return json.dumps(messages)

    def encode(self, text, **kwargs):
        return list(text.encode())

    def decode(self, ids):
        return bytes(ids).decode()


class Renderer:
    _apply_jinja_template = namespace["_apply_jinja_template"]
    _handle_last_assistant_message = namespace["_handle_last_assistant_message"]

    def __init__(self, custom=False):
        self.template_manager = SimpleNamespace(
            jinja_template_content_format="string",
            jinja_template_may_reorder_tool_results=False, reasoning_config=None,
        )
        self.tokenizer_manager = SimpleNamespace(tokenizer=Tokenizer())
        self.chat_encoding_spec = "kimi_k3" if custom else None
        self._tokenizer_auto_adds_specials = False
        self.custom_messages = None

    def _encode_messages(self, messages, *args, **kwargs):
        if self.chat_encoding_spec:
            self.custom_messages = messages
            return [42]
        return None

    def _append_assistant_prefix_to_prompt_ids(self, ids, prefix):
        return ids + list(prefix.encode())


SCHEMA = {"type": "object", "properties": {"color": {"enum": ["red", "blue"]}},
          "required": ["color"], "additionalProperties": False}


def request(messages, schema=SCHEMA, kind="json_schema", **overrides):
    values = dict(messages=[Message(m) for m in messages], chat_template_kwargs={},
                  reasoning_effort=None, continue_final_message=False, stop=None,
                  response_format=SimpleNamespace(type=kind, json_schema=SimpleNamespace(schema_=copy.deepcopy(schema))))
    values.update(overrides)
    return SimpleNamespace(**values)


class ContractRenderingTests(unittest.TestCase):
    def render(self, req, tools=None):
        renderer = Renderer()
        result = renderer._apply_jinja_template(req, tools, False)
        return renderer.tokenizer_manager.tokenizer, result

    def test_contract_reaches_tokenizer_before_conflicting_user_message(self):
        req = request([{"role": "user", "content": "The color must be green"}])
        tokenizer, _ = self.render(req)
        self.assertEqual(tokenizer.messages[0]["role"], "system")
        self.assertIn(json.dumps(SCHEMA, ensure_ascii=False, separators=(",", ":")), tokenizer.messages[0]["content"])
        self.assertEqual(tokenizer.messages[1:], req.messages)

    def test_preserves_request_messages_contract_tools_and_thinking(self):
        req = request([{"role": "system", "content": "Authorized sources only"},
                       {"role": "user", "content": "Question"}],
                      chat_template_kwargs={"enable_thinking": True})
        before = copy.deepcopy(req)
        tools = [{"type": "function", "function": {"name": "read"}}]
        tokenizer, _ = self.render(req, tools)
        self.assertEqual(req.messages, before.messages)
        self.assertEqual(req.response_format.json_schema.schema_, SCHEMA)
        self.assertEqual(tokenizer.messages[1:], before.messages)
        self.assertEqual(tokenizer.kwargs["tools"], tools)
        self.assertTrue(tokenizer.kwargs["enable_thinking"])

    def test_repeated_render_does_not_accumulate_contracts(self):
        req = request([{"role": "user", "content": "Question"}])
        first, _ = self.render(req)
        second, _ = self.render(req)
        self.assertEqual(first.messages, second.messages)
        self.assertEqual(len(second.messages), 2)

    def test_no_contract_added_to_plain_json_object_or_tool_requests(self):
        messages = [{"role": "user", "content": "Question"}]
        for fmt in (None, SimpleNamespace(type="json_object")):
            with self.subTest(fmt=fmt):
                req = request(messages, response_format=fmt)
                tokenizer, _ = self.render(req, [{"type": "function"}])
                self.assertEqual(tokenizer.messages, messages)

    def test_continuation_remains_last_in_rendered_prompt(self):
        req = request([{"role": "user", "content": "Question"},
                       {"role": "assistant", "content": '{"color":'}], continue_final_message=True)
        tokenizer, result = self.render(req)
        self.assertEqual(len(tokenizer.messages), 2)
        self.assertEqual(tokenizer.messages[-1]["role"], "user")
        self.assertTrue(bytes(result.prompt_ids).decode().endswith('{"color":'))

    def test_custom_encoder_receives_original_messages_without_duplicate_contract(self):
        req = request([{"role": "system", "content": "Instructions"},
                       {"role": "user", "content": "Question"}])
        renderer = Renderer(custom=True)
        result = renderer._apply_jinja_template(req, None, False)
        self.assertEqual(renderer.custom_messages, req.messages)
        self.assertEqual(result.prompt_ids, [42])


if __name__ == "__main__":
    unittest.main()
