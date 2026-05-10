import json
import re
import time
import asyncio
from typing import TypedDict
from google import genai
from google.genai import types
from app.config import settings
from app.models import Component, TranslationResult, RiskLevel
from app.services.rate_limiter import sync_acquire

MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF = (1, 3, 6)  # fallback if no retryDelay in 429 response

_client: genai.Client | None = None

def _get_client() -> genai.Client:
    """Return a cached Gemini client, created on first use so the key is read fresh."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client

RAG_INJECTION_MARKER = "## Original script"

FALLBACK_RESULT = TranslationResult(
    translated_script="",
    confidence=0.0,
    confidence_reasoning="Agent failed to produce a parseable response.",
    incompatible_elements=[],
    notes="Gemini response could not be parsed.",
)


class TranslationResultSchema(TypedDict):
    translated_script: str
    confidence: float
    confidence_reasoning: str
    incompatible_elements: list[str]
    notes: str


class ReviewResultSchema(TypedDict):
    approved: bool
    revised_script: str


TRANSLATION_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=TranslationResultSchema,
)

REVIEW_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    response_schema=ReviewResultSchema,
)


def _format_rag_context(chunks: list[dict]) -> str:
    """Format retrieved documentation chunks into a prompt section."""
    lines = ["## Retrieved Context (from Atlassian official documentation)"]
    for chunk in chunks:
        lines.append(f"--- source: {chunk['source']} ---")
        lines.append(chunk["content"])
        lines.append("")
    return "\n".join(lines)


class BaseAgent:
    def __init__(self):
        self.model = "gemini-2.5-flash"

    @property
    def client(self) -> genai.Client:
        return _get_client()

    def build_prompt(self, component: Component) -> str:
        raise NotImplementedError("Each agent must implement build_prompt()")

    def _get_output_ext(self, component: Component, translated_code: str) -> str:
        if translated_code.startswith("// Translation failed"):
            return "flagged"
        plugin = component.plugin.value.lower()
        if component.compatibility.cloud_status.value == "incompatible":
            return "flagged"
        if plugin in ["scriptrunner", "jsu"]:
            return ".groovy"
        elif plugin == "webhook":
            return ".py"
        elif plugin == "native":
            return ".json"
        return ".groovy"

    def _inject_rag_context(self, prompt: str, component: Component) -> str:
        """Query the RAG retriever and inject relevant context into the prompt.

        Falls back silently to the original prompt if RAG is unavailable,
        the collection is empty, or any error occurs.
        """
        try:
            from rag import get_retriever

            retriever = get_retriever()
            if retriever is None:
                return prompt

            query_text = component.original_script or ""
            if not query_text.strip():
                return prompt

            chunks = retriever.query(query_text, n_results=5)
            if not chunks:
                return prompt

            context_block = _format_rag_context(chunks)

            if RAG_INJECTION_MARKER in prompt:
                prompt = prompt.replace(
                    RAG_INJECTION_MARKER,
                    f"{context_block}\n\n{RAG_INJECTION_MARKER}",
                )
            else:
                # Fallback: prepend context if marker not found
                prompt = f"{context_block}\n\n{prompt}"

        except Exception:
            pass  # Silent fallback — agents work without RAG

        return prompt

    @staticmethod
    def _parse_retry_delay(exc: Exception) -> float | None:
        """Extract retryDelay seconds from a 429 error response.

        The Gemini API returns retryDelay in error details as a protobuf
        Duration string like '37s' or metadata. We parse it from the
        string representation of the error.
        """
        err_str = str(exc)
        # Look for patterns like "retryDelay": "37s" or retry_delay { seconds: 37 }
        match = re.search(r'retry[_-]?[Dd]elay["\s:]*["{]?\s*(\d+)s?', err_str)
        if match:
            return float(match.group(1))
        # Also check for seconds as a standalone number after "seconds:"
        match = re.search(r'seconds:\s*(\d+)', err_str)
        if match:
            return float(match.group(1))
        return None

    def _call_gemini(self, prompt: str, config: types.GenerateContentConfig = TRANSLATION_CONFIG) -> dict:
        """Call Gemini with structured output, respecting rate limits and retrying on transient errors."""
        last_exc = None
        for attempt in range(MAX_RETRIES):
            # Wait for a rate-limit slot before each attempt
            sync_acquire()
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
                return json.loads(response.text)
            except Exception as e:
                last_exc = e
                err_str = str(e)
                is_transient = any(k in err_str for k in ("503", "429", "UNAVAILABLE", "overloaded", "ReadError", "10053", "10054"))
                if not is_transient or attempt == MAX_RETRIES - 1:
                    raise

                # For 429 errors, respect the server's retryDelay hint
                if "429" in err_str:
                    server_delay = self._parse_retry_delay(e)
                    if server_delay is not None:
                        wait = server_delay
                    else:
                        wait = DEFAULT_RETRY_BACKOFF[attempt]
                else:
                    wait = DEFAULT_RETRY_BACKOFF[attempt]

                print(f"[Gemini] Transient error (attempt {attempt + 1}/{MAX_RETRIES}), retrying in {wait}s: {type(e).__name__}")
                time.sleep(wait)
        raise last_exc  # unreachable, but satisfies type checker

    def _parse_translation(self, component: Component, prompt: str) -> TranslationResult:
        """Call Gemini with structured JSON output, fallback on API/network failure."""
        try:
            parsed = self._call_gemini(prompt)
            return TranslationResult(**parsed)
        except Exception as e:
            print(f"[Agent] {component.component_id} error ({type(e).__name__}): {e}")
            return FALLBACK_RESULT

    def _check_translation_memory(self, component: Component) -> TranslationResult | None:
        """Check translation memory for a cached result.

        Returns a TranslationResult with cache fields populated on hit,
        or None on miss. Any failure falls through silently.
        """
        try:
            from rag import get_translation_memory

            memory = get_translation_memory()
            if memory is None:
                return None

            source_script = component.original_script
            if not source_script or not source_script.strip():
                return None

            hit = memory.query(source_script)
            if hit is None:
                return None

            print(f"[Cache] HIT for '{component.component_id}' (similarity={hit.similarity:.4f})")

            return TranslationResult(
                translated_script=hit.translated_script,
                confidence=hit.confidence,
                confidence_reasoning=hit.confidence_reasoning,
                incompatible_elements=hit.incompatible_elements,
                notes=hit.notes,
                confidence_label="high",
                served_from_cache=True,
                cache_similarity=hit.similarity,
                cache_warnings=hit.warnings,
            )
        except Exception as e:
            print(f"[Cache] Query FAILED for '{component.component_id}': {type(e).__name__}: {e}")
            return None

    def _store_translation(self, component_result: dict, result: TranslationResult) -> None:
        """Store a high-confidence translation in memory."""
        try:
            from rag import get_translation_memory

            memory = get_translation_memory()
            if memory is None:
                print(f"[Cache] Store skipped — TranslationMemory unavailable")
                return

            memory.store(component_result, result)
            print(f"[Cache] Stored '{component_result.get('component_id')}' (confidence={result.confidence:.2f})")
        except Exception as e:
            print(f"[Cache] Store FAILED for '{component_result.get('component_id')}': {type(e).__name__}: {e}")

    def _review_translation(self, component: Component, result: TranslationResult) -> TranslationResult:
        """Self-critique reviewer for high-risk, low-confidence translations.

        Only fires when BOTH conditions are true:
        - result.confidence < 0.80 OR len(result.incompatible_elements) > 0
        - component.compatibility.risk_level is high or critical

        Returns the (possibly revised) TranslationResult.
        """
        needs_review = result.confidence < 0.80 or len(result.incompatible_elements) > 0
        is_high_risk = component.compatibility.risk_level in (RiskLevel.high, RiskLevel.critical)

        if not (needs_review and is_high_risk):
            return result

        review_prompt = f"""Review this Jira DC-to-Cloud migration translation for correctness.

## Original script
{component.original_script or "N/A"}

## Translated script
{result.translated_script}

## Checklist
- DC-only APIs (ComponentAccessor, IssueManager, etc.) are fully replaced
- accountId is used instead of username/userkey
- REST API endpoints use v3 Cloud paths
- No leftover DC imports or classes

If the translation is correct, set approved=true and copy the translated script unchanged into revised_script.
If there are issues, set approved=false and provide the corrected script in revised_script."""

        try:
            parsed = self._call_gemini(review_prompt, config=REVIEW_CONFIG)
            if not parsed.get("approved", True) and parsed.get("revised_script", "").strip():
                print(f"[Reviewer] Corrected '{component.component_id}'")
                result.translated_script = parsed["revised_script"]
                result.confidence = max(0.0, result.confidence - 0.05)
                result.notes = (result.notes + " Revised by self-critique reviewer.").strip()
                result.reviewer_corrected = True
        except Exception as e:
            print(f"[Reviewer] {component.component_id} review failed ({type(e).__name__}): {e}")

        return result

    def translate(self, component: Component) -> dict:
        # Step 1 — Check translation memory cache
        cached = self._check_translation_memory(component)
        if cached is not None:
            translated_code = cached.translated_script
            return {
                "component_id": component.component_id,
                "plugin": component.plugin.value,
                "component_type": component.component_type.value,
                "location": component.location.model_dump(),
                "cloud_status": component.compatibility.cloud_status.value,
                "risk_level": component.compatibility.risk_level.value,
                "recommended_action": component.recommended_action,
                "translated_code": translated_code,
                "original_script": component.original_script or "N/A",
                "output_ext": self._get_output_ext(component, translated_code),
                "confidence": cached.confidence,
                "confidence_label": cached.confidence_label,
                "confidence_reasoning": cached.confidence_reasoning,
                "incompatible_elements": cached.incompatible_elements,
                "notes": cached.notes,
                "status": "success",
                "served_from_cache": True,
                "cache_similarity": cached.cache_similarity,
                "cache_warnings": cached.cache_warnings,
                "reviewer_corrected": False,
            }

        # Step 2 — RAG query + Gemini translation
        prompt = self.build_prompt(component)
        prompt = self._inject_rag_context(prompt, component)

        result = self._parse_translation(component, prompt)

        # Step 3 — Self-critique review (high-risk + low-confidence/incompatible only)
        result = self._review_translation(component, result)

        translated_code = result.translated_script
        status = "failed" if not translated_code or result.confidence == 0.0 else "success"

        component_result = {
            "component_id": component.component_id,
            "plugin": component.plugin.value,
            "component_type": component.component_type.value,
            "location": component.location.model_dump(),
            "cloud_status": component.compatibility.cloud_status.value,
            "risk_level": component.compatibility.risk_level.value,
            "recommended_action": component.recommended_action,
            "translated_code": translated_code,
            "original_script": component.original_script or "N/A",
            "output_ext": self._get_output_ext(component, translated_code),
            "confidence": result.confidence,
            "confidence_label": result.confidence_label,
            "confidence_reasoning": result.confidence_reasoning,
            "incompatible_elements": result.incompatible_elements,
            "notes": result.notes,
            "status": status,
            "served_from_cache": False,
            "cache_similarity": None,
            "cache_warnings": [],
            "reviewer_corrected": result.reviewer_corrected,
        }

        # Step 4 — Store in translation memory if high confidence
        if status == "success" and result.confidence >= 0.85:
            self._store_translation(component_result, result)

        return component_result

    async def async_translate(self, component: Component) -> dict:
        """Async wrapper that runs translate() in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.translate, component)
