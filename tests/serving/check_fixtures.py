"""Independent Draft 2020-12 oracle for the native synthetic answer cases."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "native/schema_contract_cases.json").read_text())
original = json.loads((root / "serving/final_answer_schema.json").read_text())
assert fixture["schema"] == original["parameters"], "The original schema must remain unchanged"
Draft202012Validator.check_schema(fixture["schema"])
validator = Draft202012Validator(fixture["schema"])
for case in fixture["cases"]:
    assert validator.is_valid(case["value"]) == case["valid"], case["name"]
print(f"Independent validator agrees with all {len(fixture['cases'])} synthetic cases")
