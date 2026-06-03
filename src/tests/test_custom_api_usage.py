import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm.custom_api import CustomApiGen


class FakeStreamResponse:
    def __init__(self, events):
        self.events = events

    def iter_lines(self, decode_unicode=False):
        for event in self.events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")
        yield b"data: [DONE]"


class CustomApiUsageTests(unittest.TestCase):
    def test_deepseek_payload_enables_thinking_by_default(self):
        client = CustomApiGen(
            api_key="",
            model="deepseek-v4-flash",
            api_base_url="http://example.com/v1",
            max_tokens=128,
            stream=True,
        )

        payload = client._build_payload([{"role": "user", "content": "hi"}], 0)

        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertEqual(payload["stream_options"], {"include_usage": True})

    def test_non_deepseek_payload_does_not_add_thinking_field(self):
        client = CustomApiGen(
            api_key="",
            model="gpt-4o-mini",
            api_base_url="http://example.com/v1",
            max_tokens=128,
            stream=False,
        )

        payload = client._build_payload([{"role": "user", "content": "hi"}], 0)

        self.assertNotIn("thinking", payload)

    def test_payload_replaces_lone_surrogates_before_json_send(self):
        client = CustomApiGen(
            api_key="",
            model="gpt-4o-mini",
            api_base_url="http://example.com/v1",
            max_tokens=128,
            stream=False,
        )

        payload = client._build_payload(
            [{"role": "user", "content": "bad \ud800 value"}],
            0,
        )
        encoded = json.dumps(payload)

        self.assertEqual(payload["messages"][0]["content"], "bad \uFFFD value")
        self.assertNotIn("\\ud800", encoded)
        self.assertEqual(client.last_sanitized_surrogates, 1)

    def test_stream_response_records_usage_event(self):
        client = CustomApiGen.__new__(CustomApiGen)
        client.last_usage = None
        client.last_stream_diagnostics = None
        client._current_request_label = "stream test"

        content_event = {"choices": [{"delta": {"content": "hello"}}]}
        finish_event = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        usage_event = {
            "choices": [],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        }

        reply = client._stream_response_content(
            FakeStreamResponse([content_event, finish_event, usage_event])
        )

        self.assertEqual(reply, "hello")
        self.assertEqual(client.last_usage["prompt_tokens"], 4)
        self.assertEqual(client.last_usage["completion_tokens"], 2)
        self.assertEqual(client.last_usage["total_tokens"], 6)
        self.assertEqual(client.last_usage["finish_reason"], "stop")
        self.assertEqual(client.last_stream_diagnostics["content_chunk_count"], 1)
        self.assertEqual(client.last_stream_diagnostics["content_chars"], 5)

    def test_stream_response_reads_message_content_chunk(self):
        client = CustomApiGen.__new__(CustomApiGen)
        client.last_usage = None
        client.last_stream_diagnostics = None
        client._current_request_label = "stream test"

        message_event = {"choices": [{"message": {"content": "final content"}}]}

        reply = client._stream_response_content(FakeStreamResponse([message_event]))

        self.assertEqual(reply, "final content")
        self.assertEqual(client.last_stream_diagnostics["content_chunk_count"], 1)

    def test_stream_response_records_reasoning_without_returning_it(self):
        client = CustomApiGen.__new__(CustomApiGen)
        client.last_usage = None
        client.last_stream_diagnostics = None
        client._current_request_label = "stream test"

        reasoning_event = {
            "choices": [
                {
                    "delta": {"reasoning_content": "internal reasoning"},
                    "finish_reason": "length",
                }
            ]
        }

        reply = client._stream_response_content(FakeStreamResponse([reasoning_event]))

        self.assertEqual(reply, "")
        self.assertEqual(client.last_usage["finish_reason"], "length")
        self.assertEqual(client.last_usage["reasoning_content"], "internal reasoning")
        self.assertEqual(client.last_stream_diagnostics["reasoning_chunk_count"], 1)
        self.assertEqual(client.last_stream_diagnostics["reasoning_chars"], len("internal reasoning"))
        self.assertTrue(client.last_stream_diagnostics["visible_content_empty"])


if __name__ == "__main__":
    unittest.main()
