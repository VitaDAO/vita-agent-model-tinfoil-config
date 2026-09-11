"""Build-time check of the installed wheel and the actual SGLang Qwen parser.

Runs on CPU in the candidate image. This is not a GPU inference acceptance test.
"""
import importlib.metadata
import json
from pathlib import Path

import xgrammar as xgr
from jsonschema import Draft202012Validator
from sglang.srt.entrypoints.openai.protocol import Tool
from sglang.srt.function_call.qwen3_coder_detector import Qwen3CoderDetector

assert importlib.metadata.version("xgrammar") == "0.2.1+vita3"
fixture = json.loads(Path(__file__).with_name("schema_contract_cases.json").read_text())
schema = fixture["schema"]
validator = Draft202012Validator(schema)
vocab = [chr(i) for i in range(128)] + ["<eos>"]
info = xgr.TokenizerInfo(vocab, vocab_type=xgr.VocabType.RAW, stop_token_ids=[128])
compiler = xgr.GrammarCompiler(info, max_threads=1)
compiled = compiler.compile_json_schema(schema)
structural = compiler.compile_structural_tag({
    "type": "structural_tag", "format": {
        "type": "tag", "begin": "<tool_call>\n<function=submit_final_answer>\n",
        "content": {"type": "json_schema", "style": "qwen_xml", "json_schema": schema},
        "end": "\n</function>\n</tool_call>",
    },
})
tool = Tool.model_validate({
    "type": "function", "function": {
        "name": "submit_final_answer", "parameters": schema, "strict": True,
    },
})

for case in fixture["cases"]:
    value = case["value"]
    expected = case["valid"]
    assert validator.is_valid(value) == expected, case["name"]
    matcher = xgr.GrammarMatcher(compiled)
    accepted = matcher.accept_string(json.dumps(value)) and matcher.is_completed()
    assert accepted == expected, case["name"]
    xml = ("<tool_call>\n<function=submit_final_answer>\n<parameter=answer>"
           + json.dumps(value["answer"]) + "</parameter>\n</function>\n</tool_call>")
    matcher = xgr.GrammarMatcher(structural)
    accepted = matcher.accept_string(xml) and matcher.is_completed()
    assert accepted == expected, case["name"]
    if not expected:
        continue
    parsed = Qwen3CoderDetector().detect_and_parse(xml, [tool])
    assert len(parsed.calls) == 1, case["name"]
    assert json.loads(parsed.calls[0].parameters) == value, case["name"]
    # Incremental parsing must preserve exactly the same argument object.
    for chunk_size in (1, 7, 31):
        detector = Qwen3CoderDetector()
        fragments = []
        for offset in range(0, len(xml), chunk_size):
            result = detector.parse_streaming_increment(xml[offset:offset + chunk_size], [tool])
            for call in result.calls:
                assert call.tool_index == 0
                fragments.append(call.parameters or "")
        assert json.loads("".join(fragments)) == value, (case["name"], chunk_size)

# SGLang catches RuntimeError and returns InvalidGrammarObject. Unsupported
# constraints must fail explicitly through that boundary, not produce free JSON.
try:
    compiler.compile_json_schema({
        "type": "array", "items": {"type": "integer"}, "contains": {"const": 2},
    })
except RuntimeError:
    pass
else:
    raise AssertionError("unsupported contains was silently accepted")
print("Installed compiler, original JSON/Qwen schema and incremental parser checks passed")
