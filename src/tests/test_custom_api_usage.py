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
    def test_stream_response_records_usage_event(self):
        client = CustomApiGen.__new__(CustomApiGen)
        client.last_usage = None
        client._current_request_label = "stream test"

        content_event = {"choices": [{"delta": {"content": "hello"}}]}
        usage_event = {
            "choices": [],
            "usage": {
                "prompt_tokens": 4,
                "completion_tokens": 2,
                "total_tokens": 6,
            },
        }

        reply = client._stream_response_content(FakeStreamResponse([content_event, usage_event]))

        self.assertEqual(reply, "hello")
        self.assertEqual(client.last_usage["prompt_tokens"], 4)
        self.assertEqual(client.last_usage["completion_tokens"], 2)
        self.assertEqual(client.last_usage["total_tokens"], 6)


if __name__ == "__main__":
    unittest.main()
