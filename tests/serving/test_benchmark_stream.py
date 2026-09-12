"""Transport failures must not become successful performance measurements."""
import io
import json
from pathlib import Path
import runpy
import unittest
from unittest.mock import patch

run_pass = runpy.run_path(str(Path(__file__).resolve().parents[2] / "bench_enclave.py"))["run_pass"]


def event(delta=None, finish=None):
    return {"choices": [{"delta": delta or {}, "finish_reason": finish}]}


class StreamTests(unittest.TestCase):
    def invoke(self, events, done=True):
        body = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
        if done:
            body += "data: [DONE]\n\n"
        clock = iter(range(100))
        with patch("urllib.request.urlopen", return_value=io.BytesIO(body.encode())), \
             patch("time.monotonic", side_effect=lambda: next(clock)):
            return run_pass("http://fixture/v1", "fable-distill", 800, True)

    def test_in_band_error_rejected_after_partial_output(self):
        with self.assertRaisesRegex(RuntimeError, "in-band"):
            self.invoke([event({"content": "partial"}), {"error": {"message": "aborted"}}])

    def test_premature_eof_rejected_even_with_usage(self):
        with self.assertRaisesRegex(RuntimeError, "terminal"):
            self.invoke([event({"content": "partial"}), {"usage": {"completion_tokens": 4}}], done=False)

    def test_missing_usage_is_not_replaced_with_chunk_count(self):
        with self.assertRaisesRegex(RuntimeError, "token count"):
            self.invoke([event({"content": "hello"}), event(finish="stop")])

    def test_single_burst_has_no_infinite_decode_rate(self):
        result = self.invoke([event({"content": "hello"}), event(finish="stop"),
                              {"usage": {"completion_tokens": 8}}])
        self.assertIsNone(result["tok_s"])
        self.assertGreater(result["end_to_end_tok_s"], 0)

    def test_visible_answer_timing_excludes_initial_thinking(self):
        result = self.invoke([event({"reasoning_content": "thinking"}),
                              event({"content": "answer"}), event(finish="stop"),
                              {"usage": {"completion_tokens": 12}}])
        self.assertEqual(result["ttft"], 1)
        self.assertEqual(result["time_to_visible_answer"], 2)
        self.assertEqual(result["tokens"], 12)
        self.assertEqual(result["tok_s"], 12)

    def test_completed_reasoning_without_answer_fails(self):
        with self.assertRaisesRegex(RuntimeError, "no visible answer"):
            self.invoke([event({"reasoning_content": "thinking"}), event(finish="stop"),
                         {"usage": {"completion_tokens": 12}}])

    def test_length_is_reported_for_token_limited_speed_runs(self):
        result = self.invoke([event({"content": "a"}), event({"content": "b"}),
                              event(finish="length"), {"usage": {"completion_tokens": 2}}])
        self.assertEqual(result["finish_reason"], "length")


if __name__ == "__main__":
    unittest.main()
