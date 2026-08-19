"""Dual-provider routing — BUILD-SPEC.md §16. Gemini for the reasoning-heavy calls
(claim extraction, ER adjudication, plan classification, answer synthesis); Groq for
high-volume simple calls (blocking-stage classification, negative-pair harvesting).
Two independent worker pools so their rate limits don't share a ceiling — this is
task-routing, not round-robin load balancing.
"""

from src.llm.cache import ResponseCache
from src.llm.providers import GeminiProvider, GroqProvider, LLMProvider

# NOTE: every prompt routed to Groq must mention "JSON" explicitly (its
# response_format={"type":"json_object"} mode requires it, confirmed live).
_GEMINI_TASKS = {"claim_extraction", "er_adjudication", "plan_classification", "answer_synthesis"}


class LLMRouter:
    def __init__(
        self,
        gemini: LLMProvider | None = None,
        groq: LLMProvider | None = None,
        cache: ResponseCache | None = None,
    ):
        self.gemini = gemini or GeminiProvider()
        self.groq = groq or GroqProvider()
        self.cache = cache or ResponseCache()

    def _provider_for(self, task: str) -> LLMProvider:
        return self.gemini if task in _GEMINI_TASKS else self.groq

    def complete(self, prompt: str, *, task: str, schema: dict | None = None) -> dict:
        provider = self._provider_for(task)
        cached = self.cache.get(prompt, provider.name)
        if cached is not None:
            return cached
        result = provider.complete(prompt, schema)
        self.cache.set(prompt, provider.name, result)
        return result
