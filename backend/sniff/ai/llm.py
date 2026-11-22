"""Client for any OpenAI-compatible server (llama.cpp llama-server, Ollama, the team server)."""
import re
from functools import lru_cache
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .. import config


@lru_cache(1)
def client() -> OpenAI:
    return OpenAI(base_url=config.LLM_URL, api_key="local", timeout=180)


def chat(messages: list[dict], **kw) -> str:
    r = client().chat.completions.create(model=config.LLM_MODEL, messages=messages, temperature=0, **kw)
    text = r.choices[0].message.content or ""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


DOC_CHARS = 6000


def document_message(pages: list[tuple[int, str]], task: str) -> list[dict]:
    """Document first, task last: both calls per document then share a prefix, and llama-server reuses its cache."""
    doc = "\n\n".join(f"[page {p}]\n{t}" for p, t in pages)[:DOC_CHARS]
    return [{"role": "user", "content": f"DOCUMENT\n{doc}\n\nTASK\n{task}"}]


T = TypeVar("T", bound=BaseModel)


def chat_json(messages: list[dict], model: type[T], retries: int = 2) -> T:
    """Ask for JSON matching `model` (the server constrains output to the schema), validate, retry with the error."""
    fmt = {"type": "json_schema", "json_schema": {"name": model.__name__, "schema": model.model_json_schema()}}
    for attempt in range(retries + 1):
        text = chat(messages, response_format=fmt)
        try:
            return model.model_validate_json(text)
        except ValidationError as e:
            if attempt == retries:
                raise
            messages = messages + [
                {"role": "assistant", "content": text},
                {"role": "user", "content": f"That JSON is invalid: {e.errors()[:3]}. Reply with corrected JSON only."},
            ]
    raise AssertionError("unreachable")
