"""Plain-text rewrites of one selected paragraph using the shared local model."""

import re
from typing import Protocol

from services.llm.completion import plain_text_completion
from services.llm.scheduler import ModelScheduler


MAX_ENHANCEMENT_INPUT_CHARS = 2_000
MAX_ENHANCEMENT_OUTPUT_CHARS = 4_000
ENHANCEMENT_OUTPUT_TOKENS = 384

EXAMPLES = (
    "Improve clarity and grammar while preserving meaning, facts, names, numbers, "
    "language, and tone. Keep every named person, date, and quantity. "
    "Return only one rewritten paragraph, without labels or commentary.\n"
    "Source: She don't have enough time to finish the report.\n"
    "Rewrite: She doesn't have enough time to finish the report.\n"
    "Source: The team met on Tuesday, and they talked about the plan for the launch.\n"
    "Rewrite: The team met on Tuesday to discuss the launch plan.\n"
    "Source: On May 3, Lina said the park got 12 new chairs and the team was happy about it.\n"
    "Rewrite: On May 3, Lina said the park received 12 new chairs, which pleased the team.\n"
)


def enhancement_prompt(source: str) -> str:
    return f"{EXAMPLES}Source: {source}\nRewrite:"


def clean_enhancement(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("The model returned no rewrite text.")
    text = plain_text_completion(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^\s*(?:rewrite|improved)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text or len(text) > MAX_ENHANCEMENT_OUTPUT_CHARS or "\n" in text:
        raise ValueError("The model returned an empty, multiline, or oversized rewrite.")
    if any(ord(character) < 32 for character in text):
        raise ValueError("The model returned control characters.")
    return text


class EnhancementEngine(Protocol):
    async def improve(self, text: str, scheduler: ModelScheduler) -> str: ...


class LlamaEnhancementEngine:
    def __init__(self, model, context_size: int):
        self.model = model
        self.context_size = context_size

    def _generate(self, text: str) -> str:
        prompt = enhancement_prompt(text)
        if len(self.model.tokenize(prompt.encode("utf-8"), add_bos=False)) + ENHANCEMENT_OUTPUT_TOKENS > self.context_size:
            raise ValueError("The selection does not fit the model context.")
        self.model.reset()
        result = self.model.create_completion(
            prompt=prompt,
            max_tokens=ENHANCEMENT_OUTPUT_TOKENS,
            temperature=0.25,
            top_p=0.9,
            stop=["\n", "Source:"],
        )
        return clean_enhancement(result["choices"][0]["text"])

    async def improve(self, text: str, scheduler: ModelScheduler) -> str:
        return await scheduler.run(self._generate, text)
