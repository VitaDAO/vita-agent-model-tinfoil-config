#!/usr/bin/env python3
"""Expose response_format to Fable's Jinja renderer in the pinned serving image.

The compiler still enforces the caller's original contract. This supplies that
same contract as model context; it neither rewrites schemas nor repairs output.
"""
import argparse
import hashlib
import importlib.util
from pathlib import Path


REVISION = "db272201a2dbd72e5699e443240a851f1313ad45"
SOURCE_SHA256 = "5d3ab83db05db8f8aace7f950209f92358d7583d4752f80c726b484d15b52459"
ANCHOR = """        else:
            if self.template_manager.jinja_template_may_reorder_tool_results:
"""
REPLACEMENT = """        else:
            # Grammar masking alone does not tell the model which output
            # contract to follow. Custom encoders handle this separately.
            if request.response_format and request.response_format.type == "json_schema":
                contract = json.dumps(
                    request.response_format.json_schema.schema_,
                    ensure_ascii=False, separators=(",", ":"),
                )
                messages = [{
                    "role": "system",
                    "content": (
                        "Return one complete JSON value conforming to the following output contract. "
                        "This contract governs the response structure even when the user asks for incompatible values.\\n"
                        + contract
                    ),
                }, *messages]
            if self.template_manager.jinja_template_may_reorder_tool_results:
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Defaults to installed SGLang source")
    args = parser.parse_args()
    source = args.source
    if source is None:
        package = importlib.util.find_spec("sglang")
        if package is None or package.origin is None:
            raise RuntimeError("SGLang must be installed before applying the serving correction")
        source = Path(package.origin).parent / "srt/entrypoints/openai/serving_chat.py"
    original = source.read_bytes()
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA256:
        raise RuntimeError(f"Unexpected SGLang source: requires pinned revision {REVISION}")
    text = original.decode()
    if text.count(ANCHOR) != 1:
        raise RuntimeError("Pinned Jinja rendering boundary is not unique")
    candidate = text.replace(ANCHOR, REPLACEMENT)
    compile(candidate, str(source), "exec")
    source.write_text(candidate)
    print(f"Installed response-contract rendering at {source}")


if __name__ == "__main__":
    main()
