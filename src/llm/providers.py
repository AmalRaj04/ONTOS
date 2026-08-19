"""BUILD-SPEC.md §16 — Gemini + Groq, one interface for both: `.complete(prompt,
schema) -> dict`.

Model names confirmed live against each provider (both moved on from what the spec's
own text assumed — see PROJECT.md decision #23):
- Gemini: `gemini-2.5-flash` is decommissioned; `gemini-3.6-flash` works.
- Groq: no `llama-3.1-*` models are served any more; `openai/gpt-oss-20b` works and
  is the fast/high-volume model the §16 table calls for.
"""

import json
import os
from abc import ABC, abstractmethod

from google import genai
from groq import Groq

GEMINI_MODEL = "gemini-3.6-flash"
GROQ_MODEL = "openai/gpt-oss-20b"


def _parse_json(text: str):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


def _normalize(parsed) -> dict:
    if isinstance(parsed, dict):
        return parsed
    return {"items": parsed}


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def complete(self, prompt: str, schema: dict | None = None) -> dict:
        """Returns parsed JSON as a dict. A top-level JSON array is wrapped as
        {"items": [...]}."""


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str = GEMINI_MODEL):
        self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self._model = model

    def complete(self, prompt: str, schema: dict | None = None) -> dict:
        config = {"response_mime_type": "application/json"}
        if schema is not None:
            config["response_schema"] = schema
        resp = self._client.models.generate_content(
            model=self._model, contents=prompt, config=config
        )
        return _normalize(_parse_json(resp.text))


class GroqProvider(LLMProvider):
    name = "groq"

    def __init__(self, api_key: str | None = None, model: str = GROQ_MODEL):
        self._client = Groq(api_key=api_key or os.environ["GROQ_API_KEY"])
        self._model = model

    def complete(self, prompt: str, schema: dict | None = None) -> dict:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return _normalize(_parse_json(resp.choices[0].message.content))
