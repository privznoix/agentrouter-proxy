import importlib.util
import json
import pathlib
import time
import unittest

import httpx
from starlette.requests import Request


MODULE_PATH = pathlib.Path(__file__).with_name("agentrouter-proxy.py")
SPEC = importlib.util.spec_from_file_location("agentrouter_proxy", MODULE_PATH)
proxy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(proxy)


class ResponsesCompatibilityTests(unittest.TestCase):
    def test_only_astra_routes_to_responses(self):
        self.assertTrue(proxy.uses_responses_api("gpt-6-astra"))
        self.assertTrue(proxy.uses_responses_api("gpt-6-astra-2026-09-01"))
        self.assertFalse(proxy.uses_responses_api("gpt-6-sol"))

    def test_chat_payload_converts_tools_history_and_parameters(self):
        payload = {
            "model": "gpt-6-astra",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "Weather?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": "{\"city\":\"Jakarta\"}",
                        },
                    }],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "content": "31 C",
                },
            ],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                    "strict": True,
                },
            }],
            "tool_choice": {
                "type": "function",
                "function": {"name": "get_weather"},
            },
            "reasoning_effort": "high",
            "max_completion_tokens": 500,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "weather",
                    "strict": True,
                    "schema": {"type": "object"},
                },
            },
            "temperature": 0.2,
        }

        result = proxy.chat_payload_to_responses(payload)

        self.assertEqual(result["reasoning"], {"effort": "high"})
        self.assertEqual(result["max_output_tokens"], 500)
        self.assertNotIn("temperature", result)
        self.assertEqual(result["tools"][0]["name"], "get_weather")
        self.assertNotIn("function", result["tools"][0])
        self.assertEqual(
            result["tool_choice"],
            {"type": "function", "name": "get_weather"},
        )
        self.assertEqual(result["input"][2]["type"], "function_call")
        self.assertEqual(result["input"][3], {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "31 C",
        })
        self.assertEqual(result["text"]["format"]["name"], "weather")

    def test_astra_none_reasoning_is_raised_to_low(self):
        result = proxy.chat_payload_to_responses({
            "model": "gpt-6-astra",
            "messages": [{"role": "user", "content": "Hello"}],
            "reasoning_effort": "none",
        })
        self.assertEqual(result["reasoning"], {"effort": "low"})

    def test_non_streaming_response_converts_text_tool_calls_and_usage(self):
        response = {
            "id": "resp_1",
            "created_at": 123,
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Checking "}],
                },
                {
                    "type": "function_call",
                    "call_id": "call_7",
                    "name": "lookup",
                    "arguments": "{\"id\":7}",
                },
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 4,
                "total_tokens": 14,
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        }

        result = proxy.responses_json_to_chat(response, "arp/gpt-6-astra")

        choice = result["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(choice["message"]["content"], "Checking ")
        self.assertEqual(choice["message"]["tool_calls"][0]["id"], "call_7")
        self.assertEqual(result["usage"]["prompt_tokens"], 10)
        self.assertEqual(
            result["usage"]["completion_tokens_details"]["reasoning_tokens"],
            2,
        )


class ResponsesStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def _translate(self, payloads, split_at=None):
        wire = b"".join(
            b"data: " + json.dumps(payload).encode("utf-8") + b"\n\n"
            for payload in payloads
        )
        if split_at is None:
            split_at = len(wire)

        async def source():
            yield wire[:split_at]
            if split_at < len(wire):
                yield wire[split_at:]

        output = []
        async for chunk in proxy.translate_responses_stream(
            source(),
            "arp/gpt-6-astra",
        ):
            output.append(chunk.decode("utf-8"))
        return output

    async def test_fragmented_text_stream_becomes_chat_chunks(self):
        chunks = await self._translate([
            {
                "type": "response.created",
                "response": {"id": "resp_2", "created_at": 456},
            },
            {"type": "response.output_text.delta", "delta": "Halo"},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            },
        ], split_at=17)

        joined = "".join(chunks)
        self.assertIn('"role":"assistant"', joined)
        self.assertIn('"content":"Halo"', joined)
        self.assertIn('"finish_reason":"stop"', joined)
        self.assertEqual(joined.count("data: [DONE]"), 1)

    async def test_function_stream_becomes_tool_call_chunks(self):
        chunks = await self._translate([
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_9",
                    "name": "lookup",
                    "arguments": "",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 1,
                "item_id": "fc_1",
                "delta": "{\"id\":9}",
            },
            {
                "type": "response.completed",
                "response": {"status": "completed"},
            },
        ])

        joined = "".join(chunks)
        self.assertIn('"id":"call_9"', joined)
        self.assertIn('"name":"lookup"', joined)
        self.assertIn('"arguments":"{\\\"id\\\":9}"', joined)
        self.assertIn('"finish_reason":"tool_calls"', joined)


class AstraRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_astra_posts_to_responses_and_returns_chat_shape(self):
        captured = {}

        class FakeClient:
            def __init__(self, timeout):
                self.timeout = timeout

            def build_request(self, method, url, headers=None, json=None):
                captured["url"] = url
                captured["json"] = json
                return httpx.Request(method, url, headers=headers, json=json)

            async def send(self, request, stream=False):
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "id": "resp_route",
                        "created_at": 789,
                        "status": "completed",
                        "output": [{
                            "type": "message",
                            "role": "assistant",
                            "content": [{
                                "type": "output_text",
                                "text": "OK",
                            }],
                        }],
                    },
                )

            async def aclose(self):
                return None

        body = json.dumps({
            "model": "arp/gpt-6-astra",
            "messages": [{"role": "user", "content": "Hello"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "noop",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
            "reasoning_effort": "medium",
        }).encode("utf-8")
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.request", "body": b"", "more_body": False}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": []}, receive)

        old_key = proxy.UPSTREAM_API_KEY
        old_models = proxy._registry_models
        old_fetched = proxy._registry_fetched_at
        old_client = proxy.httpx.AsyncClient
        try:
            proxy.UPSTREAM_API_KEY = "test-key"
            proxy._registry_models = [{"id": "gpt-6-astra"}]
            proxy._registry_fetched_at = time.time()
            proxy.httpx.AsyncClient = FakeClient

            response = await proxy.chat_completions(
                request,
                authorization="Bearer local-agentrouter",
            )
        finally:
            proxy.UPSTREAM_API_KEY = old_key
            proxy._registry_models = old_models
            proxy._registry_fetched_at = old_fetched
            proxy.httpx.AsyncClient = old_client

        self.assertTrue(captured["url"].endswith("/responses"))
        self.assertIn("input", captured["json"])
        self.assertNotIn("messages", captured["json"])
        result = json.loads(response.body)
        self.assertEqual(result["object"], "chat.completion")
        self.assertEqual(result["choices"][0]["message"]["content"], "OK")


if __name__ == "__main__":
    unittest.main()
