"""Local, synchronous llama.cpp text completion adapter."""

import os
import re
from html import unescape
from html.parser import HTMLParser
from typing import Protocol


MAX_INPUT_CHARS = 16_000
MAX_OUTPUT_CHARS = 600
INFILL_BEFORE_CHARS = 200
INFILL_AFTER_CHARS = 120
INFILL_EXAMPLES = (
    "Fill the gap with only the missing words.\n"
    "I will meet you at [gap] => noon.\n"
    "She opened the [gap] and stepped inside. => door\n"
)


class _PlainTextParser(HTMLParser):
    """Keep visible text from model-generated HTML fragments."""

    _BLOCK_TAGS = {
        "br", "div", "p", "li", "ul", "ol", "blockquote",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.pending_space = False
        self.hidden_tag: str | None = None

    def handle_starttag(self, tag: str, attrs):
        if tag in {"script", "style"}:
            self.hidden_tag = tag
        elif tag in self._BLOCK_TAGS:
            self.pending_space = True

    def handle_endtag(self, tag: str):
        if tag == self.hidden_tag:
            self.hidden_tag = None
        elif tag in self._BLOCK_TAGS:
            self.pending_space = True

    def handle_data(self, data: str):
        if self.hidden_tag or not data:
            return
        if (
            self.pending_space
            and self.parts
            and not self.parts[-1][-1].isspace()
            and not data[0].isspace()
        ):
            self.parts.append(" ")
        self.parts.append(data)
        self.pending_space = False


def plain_text_completion(value: str) -> str:
    """Remove markup while preserving the words and spacing of a suggestion."""
    parser = _PlainTextParser()
    parser.feed(unescape(value))
    parser.close()
    return "".join(parser.parts)


class CompletionEngine(Protocol):
    def generate(self, prefix: str, suffix: str) -> str: ...


class LlamaCompletionEngine:
    def __init__(self, model_path: str):
        from llama_cpp import Llama

        self.model = Llama(
            model_path=model_path,
            n_ctx=int(os.environ.get("LLM_N_CTX", "4096")),
            n_threads=int(os.environ.get("LLM_N_THREADS", "4")),
            n_gpu_layers=int(os.environ.get("LLM_N_GPU_LAYERS", "0")),
            verbose=False,
        )

    def generate(self, prefix: str, suffix: str) -> str:
        # Keep each request independent, including llama.cpp's reusable model state.
        self.model.reset()
        infill = bool(suffix.strip()) and not suffix.lstrip(" \t").startswith("\n")
        if infill:
            before = prefix[-INFILL_BEFORE_CHARS:].replace("\n", " ")
            after = suffix[:INFILL_AFTER_CHARS].replace("\n", " ").lstrip()
            prompt = f"{INFILL_EXAMPLES}{before} [gap] {after} =>"
        else:
            prompt = prefix
        result = self.model.create_completion(
            prompt=prompt,
            max_tokens=12,
            temperature=0.7,
            top_p=0.95,
            stop=["\n"],
        )
        return prepare_completion(result["choices"][0]["text"], prefix, suffix, infill)


def prepare_completion(value: str, prefix: str, suffix: str, infill: bool) -> str:
    """Keep one short insertion and fit its spaces to the cursor boundaries."""
    text = plain_text_completion(value).split("\n", 1)[0]
    if not infill:
        ending = re.search(r"[.!?]", text)
        if ending:
            text = text[: ending.end()]
    if prefix.endswith((" ", "\t")):
        text = text.lstrip(" \t")
    elif prefix and prefix[-1].isalnum() and text and text[0].isalnum():
        text = " " + text
    if infill and suffix.startswith((" ", "\t")):
        text = text.rstrip(" \t")
    return text


def clean_completion(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("The model returned no text.")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or len(text) > MAX_OUTPUT_CHARS:
        raise ValueError("The model returned an empty or oversized completion.")
    if any(ord(character) < 32 and character not in "\n\t" for character in text):
        raise ValueError("The model returned control characters.")
    return text


def is_insertable_completion(text: str, prefix: str, suffix: str = "") -> bool:
    """Reject accidental copies of surrounding text."""
    candidate = text.lstrip()
    line = prefix.rstrip().rsplit("\n", 1)[-1]
    full_line = line.strip()
    if len(full_line) >= 10 and candidate.casefold().startswith(full_line.casefold()):
        rest = candidate[len(full_line):]
        if not rest or not rest[0].isalnum():
            return False
    words = line.split()
    if len(words) >= 3:
        repeated = " ".join(words[-3:])
        if len(repeated) >= 10 and candidate.casefold().startswith(repeated.casefold()):
            rest = candidate[len(repeated):]
            if not rest or not rest[0].isalnum():
                return False
    after = suffix.lstrip()
    without_terminal = candidate.rstrip(".!? ")
    if len(without_terminal) >= 8 and after.casefold().startswith(without_terminal.casefold()):
        rest = after[len(without_terminal):]
        if not rest or not rest[0].isalnum():
            return False
    return True
