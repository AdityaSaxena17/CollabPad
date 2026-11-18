"""Verify the provider's Converse requests without calling AWS."""

import unittest
from unittest.mock import Mock

from services.llm.bedrock import BedrockEngine, DEFAULT_COMPLETION_MODEL, DEFAULT_WRITING_MODEL
from services.llm.scheduler import ModelScheduler


class BedrockEngineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = Mock()
        self.client.converse.return_value = {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": "the draft."}]}},
        }
        self.engine = BedrockEngine(self.client, DEFAULT_COMPLETION_MODEL, DEFAULT_WRITING_MODEL)

    def test_completion_uses_micro_and_limits_output(self):
        self.assertEqual(self.engine.generate("Review ", ""), "the draft.")
        request = self.client.converse.call_args.kwargs
        self.assertEqual(request["modelId"], "amazon.nova-micro-v1:0")
        self.assertEqual(request["inferenceConfig"]["maxTokens"], 16)

    async def test_writing_uses_lite_and_distinct_grammar_instruction(self):
        scheduler = ModelScheduler()
        self.assertEqual(await self.engine.summarize("A draft.", scheduler), "the draft.")
        self.assertEqual(await self.engine.improve("A draft.", scheduler), "the draft.")
        self.assertEqual(await self.engine.correct_grammar("A draft.", scheduler), "the draft.")
        requests = [call.kwargs for call in self.client.converse.call_args_list]
        self.assertTrue(all(req["modelId"] == "amazon.nova-lite-v1:0" for req in requests))
        self.assertIn("Improve clarity", requests[1]["system"][0]["text"])
        self.assertIn("Correct only grammar", requests[2]["system"][0]["text"])

    async def test_long_summary_chunks_before_combining(self):
        await self.engine.summarize("é" * 10_000, ModelScheduler())
        requests = [call.kwargs for call in self.client.converse.call_args_list]
        self.assertEqual([req["inferenceConfig"]["maxTokens"] for req in requests], [128, 128, 256])

    def test_rejects_filtered_generation(self):
        self.client.converse.return_value = {"stopReason": "content_filtered"}
        with self.assertRaises(ValueError):
            self.engine.generate("Draft ", "")
