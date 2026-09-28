"""Chunked, plain-text document summaries using the shared llama.cpp model."""

import re
from collections.abc import Awaitable, Callable
from typing import Protocol

from services.llm.completion import plain_text_completion
from services.llm.scheduler import ModelScheduler


MAX_SUMMARY_INPUT_CHARS = 100_000
MAX_SUMMARY_OUTPUT_CHARS = 2_000
SUMMARY_JOB_TTL_SECONDS = 30 * 60
SUMMARY_JOB_LIMIT = 4

SUMMARY_EXAMPLES = (
    "Document: The city opened three free parks this spring. Residents can visit "
    "them every day.\n\nOfficials will add bike lanes next year.\n"
    "Summary: The city opened three free parks and plans to add bike lanes next year.\n\n"
    "Document: Maya studied rainfall for ten years. Her report found drier summers.\n\n"
    "She recommended more water storage, and her team will repeat the study.\n"
    "Summary: A ten-year study found drier summers. Maya recommended more water "
    "storage, and her team plans to repeat the study.\n\n"
)


def summary_prompt(source: str) -> str:
    return f"{SUMMARY_EXAMPLES}Document: {source}\nSummary:"


class SummaryEngine(Protocol):
    async def summarize(self, text: str, scheduler: ModelScheduler) -> str: ...


def clean_summary(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("The model returned no summary text.")
    text = plain_text_completion(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^\s*summary\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n(?:\s*\n)+", "\n\n", text).strip()
    if not text or len(text) > MAX_SUMMARY_OUTPUT_CHARS:
        raise ValueError("The model returned an empty or oversized summary.")
    if any(ord(character) < 32 and character not in "\n\t" for character in text):
        raise ValueError("The model returned control characters.")
    return text


async def split_for_context(
    text: str, budget: int, count_tokens: Callable[[str], Awaitable[int]]
) -> list[str]:
    """Fit each chunk to the tokenizer, preferring paragraph and word boundaries."""
    if budget < 1:
        raise ValueError("The model context is too small for this text.")
    remaining = text.strip()
    pieces: list[str] = []
    while remaining:
        if await count_tokens(remaining) <= budget:
            pieces.append(remaining)
            break
        low, high, best = 1, len(remaining), 0
        while low <= high:
            middle = (low + high) // 2
            if await count_tokens(remaining[:middle]) <= budget:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        if best == 0:
            raise ValueError("The model context is too small for this text.")
        candidate = remaining[:best]
        paragraph = candidate.rfind("\n\n")
        word = max(candidate.rfind(" "), candidate.rfind("\n"))
        boundary = paragraph if paragraph >= best // 2 else word
        cut = boundary if boundary >= best // 2 else best
        piece = remaining[:cut].strip()
        if not piece:
            cut = best
            piece = remaining[:cut].strip()
        if await count_tokens(piece) > budget:
            cut = best
            piece = remaining[:cut].strip()
        pieces.append(piece)
        remaining = remaining[cut:].strip()
    return pieces


class LlamaSummaryEngine:
    def __init__(self, model, context_size: int):
        self.model = model
        self.context_size = context_size

    def _count_tokens(self, text: str) -> int:
        return len(self.model.tokenize(text.encode("utf-8"), add_bos=False))

    def _generate(self, prompt: str, max_tokens: int) -> str:
        self.model.reset()
        result = self.model.create_completion(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=0.2,
            top_p=0.9,
            stop=["\n\n", "\nDocument:"],
        )
        return clean_summary(result["choices"][0]["text"])

    async def summarize(self, text: str, scheduler: ModelScheduler) -> str:
        if self.context_size < 1024:
            raise ValueError("LLM_N_CTX must be at least 1024 for summaries.")
        budget = self.context_size - 768

        async def count(value: str) -> int:
            return await scheduler.run(self._count_tokens, value)

        chunks = await split_for_context(text, budget, count)
        if len(chunks) > 1:
            chunks = [
                await scheduler.run(self._generate, summary_prompt(chunk), 128)
                for chunk in chunks
            ]
        combined = "\n\n".join(chunks)
        while await count(combined) > budget:
            reduced = await split_for_context(combined, budget, count)
            if len(reduced) < 2:
                raise ValueError("The summary could not fit the model context.")
            chunks = [
                await scheduler.run(self._generate, summary_prompt(chunk), 128)
                for chunk in reduced
            ]
            next_combined = "\n\n".join(chunks)
            if len(next_combined) >= len(combined):
                raise ValueError("The summary did not shrink enough to fit the model context.")
            combined = next_combined
        return await scheduler.run(
            self._generate, summary_prompt(combined), 256
        )
