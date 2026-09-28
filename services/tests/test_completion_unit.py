"""Model adapter and completion validation without a GGUF file."""

import os
import sys
import asyncio
import threading
import types
import unittest
from unittest.mock import patch

import grpc

from services.llm.completion import (
    LlamaCompletionEngine,
    clean_completion,
    is_insertable_completion,
    plain_text_completion,
    prepare_completion,
)
from services.llm.service import LlmServicer
from services.proto import llm_pb2


class Aborted(Exception):
    def __init__(self, code):
        self.code = code


class FakeContext:
    async def abort(self, code, _details):
        raise Aborted(code)


class CompletionUnitTest(unittest.TestCase):
    def test_plain_text_completion_keeps_words_and_spacing(self):
        for model_text, expected in (
            (" <em>dark</em> road.", " dark road."),
            ("<b>very <i>dark</i></b>.", "very dark."),
            ('<a href="example.com">road</a>.', "road."),
            ("x &amp; y", "x & y"),
            ("&lt;em&gt;dark&lt;/em&gt; road", "dark road"),
            ("hello<br>world", "hello world"),
            ("<p>first</p><p>second</p>", "first second"),
            ("x < 3", "x < 3"),
            ("safe<script>alert('x')</script> text", "safe text"),
            ("<b></b>", ""),
        ):
            with self.subTest(model_text=model_text):
                self.assertEqual(plain_text_completion(model_text), expected)

    def test_validation_preserves_spaces_and_line_breaks(self):
        self.assertEqual(clean_completion(" next\r\nline"), " next\nline")
        for invalid in (None, " ", "x" * 601, "bad\x00text"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    clean_completion(invalid)

    def test_adapter_prompts_for_short_inline_insertions(self):
        calls = []

        class FakeLlama:
            def __init__(self, **kwargs):
                calls.append(kwargs)

            def reset(self):
                calls.append("reset")

            def create_completion(self, **kwargs):
                calls.append(kwargs)
                text = "table" if "[gap]" in kwargs["prompt"] else "?**"
                return {"choices": [{"text": text}]}

        with patch.dict(sys.modules, {"llama_cpp": types.SimpleNamespace(Llama=FakeLlama)}):
            with patch.dict(os.environ, {"LLM_N_CTX": "4096", "LLM_N_THREADS": "2"}):
                engine = LlamaCompletionEngine("/models/model.gguf")
                self.assertEqual(engine.generate("where are you", ""), "?")
                self.assertEqual(engine.generate("She put the book on the", " before leaving."), " table")
                self.assertEqual(engine.generate("another phrase", ""), "?")
        self.assertEqual(calls[0]["model_path"], "/models/model.gguf")
        self.assertEqual(calls[1], "reset")
        self.assertEqual(calls[3], "reset")
        self.assertEqual(calls[5], "reset")
        self.assertEqual(calls[2]["max_tokens"], 12)
        self.assertEqual(calls[2]["temperature"], 0.7)
        self.assertEqual(calls[2]["top_p"], 0.95)
        self.assertEqual(calls[2]["stop"], ["\n"])
        self.assertEqual(calls[2]["prompt"], "where are you")
        self.assertIn("She put the book on the [gap] before leaving. =>", calls[4]["prompt"])
        self.assertIn("She opened the [gap] and stepped inside. => door", calls[4]["prompt"])
        self.assertNotIn("where are you", calls[4]["prompt"])
        self.assertEqual(calls[6]["prompt"], "another phrase")

    def test_completion_formatting_and_bounded_infill_context(self):
        self.assertEqual(prepare_completion("?**", "where are you", "", False), "?")
        self.assertEqual(prepare_completion("table ", "She put the book on the", " before leaving.", True), " table")
        self.assertEqual(prepare_completion(" table", "She put the book on ", " before leaving.", True), "table")
        self.assertEqual(prepare_completion("York", "New", "", False), " York")
        self.assertEqual(
            prepare_completion(' <a href="example.com">dark</a>. more', "the", "", False),
            " dark.",
        )

        prompts = []

        class FakeLlama:
            def __init__(self, **_kwargs):
                pass

            def reset(self):
                pass

            def create_completion(self, **kwargs):
                prompts.append(kwargs["prompt"])
                return {"choices": [{"text": " answer"}]}

        with patch.dict(sys.modules, {"llama_cpp": types.SimpleNamespace(Llama=FakeLlama)}):
            LlamaCompletionEngine("/models/model.gguf").generate("x" * 250, " y" * 100)
        self.assertIn(f"{'x' * 200} [gap]", prompts[0])
        self.assertNotIn("x" * 201, prompts[0])
        self.assertNotIn(" y" * 61, prompts[0])

    def test_allows_model_text_but_rejects_copied_context(self):
        for candidate in (
            " going?", "?", " table", "\nThe next line",
            "Sure thing!", "Response: next", "0% visibility.",
        ):
            with self.subTest(candidate=candidate):
                self.assertTrue(is_insertable_completion(candidate, "where are you"))
        self.assertTrue(is_insertable_completion("York", "New"))
        self.assertFalse(is_insertable_completion("where are you?", "where are you"))
        self.assertFalse(is_insertable_completion("She put the book on the table.", "She put the book on the"))
        self.assertFalse(is_insertable_completion(" before leaving", "She put the book on the", " before leaving."))


class CompletionServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_completion_passes_model_text_and_suppresses_blank(self):
        class ReplyEngine:
            def __init__(self, reply):
                self.reply = reply

            def generate(self, _prefix, _suffix):
                return self.reply

        async def immediate(worker, *args):
            return worker(*args)

        with patch("services.llm.service.asyncio.to_thread", new=immediate):
            for reply, expected in (
                ("Sure thing!", "Sure thing!"),
                ("Response: next", "Response: next"),
                ("0% visibility.", "0% visibility."),
                ("<em>dark</em> road", "dark road"),
                ("<b>very <i>dark</i></b>", "very dark"),
                ("<b></b>", ""),
                ("  ", ""),
            ):
                with self.subTest(reply=reply):
                    result = await LlmServicer(ReplyEngine(reply)).Complete(
                        llm_pb2.CompleteRequest(prefix="where are you"), FakeContext()
                    )
                    self.assertEqual(result.text, expected)

    async def test_sanitized_output_still_obeys_output_limits(self):
        class ReplyEngine:
            def __init__(self, reply):
                self.reply = reply

            def generate(self, _prefix, _suffix):
                return self.reply

        async def immediate(worker, *args):
            return worker(*args)

        with patch("services.llm.service.asyncio.to_thread", new=immediate):
            for reply in ("<em>" + "x" * 601 + "</em>", "<b>bad\x00text</b>"):
                with self.subTest(reply=reply):
                    with self.assertRaises(Aborted) as raised:
                        await LlmServicer(ReplyEngine(reply)).Complete(
                            llm_pb2.CompleteRequest(prefix="hello"), FakeContext()
                        )
                    self.assertEqual(raised.exception.code, grpc.StatusCode.INTERNAL)

    async def test_validation_and_unconfigured_model(self):
        service = LlmServicer()
        context = FakeContext()
        for request, code in (
            (llm_pb2.CompleteRequest(prefix=" "), grpc.StatusCode.INVALID_ARGUMENT),
            (llm_pb2.CompleteRequest(prefix="x" * 16_001), grpc.StatusCode.RESOURCE_EXHAUSTED),
            (llm_pb2.CompleteRequest(prefix="hello"), grpc.StatusCode.UNAVAILABLE),
        ):
            with self.subTest(code=code):
                with self.assertRaises(Aborted) as raised:
                    await service.Complete(request, context)
                self.assertEqual(raised.exception.code, code)

    async def test_serializes_model_and_rejects_invalid_output(self):
        started = threading.Event()
        release = threading.Event()

        class SlowEngine:
            def generate(self, _prefix, _suffix):
                started.set()
                release.wait(2)
                return " continuation"

        service = LlmServicer(SlowEngine())
        request = llm_pb2.CompleteRequest(prefix="hello")
        first = asyncio.create_task(service.Complete(request, FakeContext()))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            with self.assertRaises(Aborted) as raised:
                await service.Complete(request, FakeContext())
            self.assertEqual(raised.exception.code, grpc.StatusCode.UNAVAILABLE)
        finally:
            release.set()
        self.assertEqual((await first).text, " continuation")

        class BadEngine:
            def generate(self, _prefix, _suffix):
                return "\x00"

        with self.assertRaises(Aborted) as raised:
            await LlmServicer(BadEngine()).Complete(request, FakeContext())
        self.assertEqual(raised.exception.code, grpc.StatusCode.INTERNAL)

    async def test_cancellation_keeps_model_locked_until_worker_finishes(self):
        started = threading.Event()
        release = threading.Event()

        class SlowEngine:
            def generate(self, _prefix, _suffix):
                started.set()
                release.wait(2)
                return " done"

        service = LlmServicer(SlowEngine())
        request = llm_pb2.CompleteRequest(prefix="hello")
        first = asyncio.create_task(service.Complete(request, FakeContext()))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            with self.assertRaises(Aborted) as raised:
                await service.Complete(request, FakeContext())
            self.assertEqual(raised.exception.code, grpc.StatusCode.UNAVAILABLE)
        finally:
            release.set()
        for _ in range(20):
            if not service.completion_lock.locked():
                break
            await asyncio.sleep(0.01)
        self.assertFalse(service.completion_lock.locked())
