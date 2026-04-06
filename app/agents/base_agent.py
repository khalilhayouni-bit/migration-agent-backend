import re
import json
from google import genai
from app.config import settings
from app.models import Component, TranslationResult

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
    notes="Gemini response could not be parsed after retry.",
)

JSON_OUTPUT_INSTRUCTIONS = """
## Response format
You MUST respond with ONLY a single raw JSON object. No markdown fences, no preamble, no explanation.
The JSON object must have exactly these fields:

{
  "translated_script": "<the full translated code as a single string>",
  "confidence": <float between 0.0 and 1.0>,
  "confidence_reasoning": "<1-2 sentences explaining what drove the confidence score up or down, referencing specific constructs from the source script and whether retrieved RAG context was sufficient>",
  "incompatible_elements": ["<specific DC construct> — <why incompatible and what replacement was used>"],
  "notes": "<any additional migration notes>"
}

Rules for each field:
- translated_script: The complete translated code. Must be valid and runnable.
- confidence: Float between 0.0 and 1.0.
  - Above 0.80 = confident the translation is correct and runnable
  - 0.50 to 0.80 = uncertain, manual review recommended
  - Below 0.50 = could not reliably translate, high risk
- confidence_reasoning: One or two sentences max. Must reference specific constructs from the source script. Must mention whether retrieved context was sufficient or insufficient.
- incompatible_elements: List of strings. Each must be specific, e.g. "ComponentAccessor.getIssueManager() — no direct Cloud equivalent, rewritten as REST call to /rest/api/3/issue". Empty list if fully translatable.
- notes: Any additional context about the migration.

Return ONLY the raw JSON object. No markdown. No explanation. No code fences.
"""

RETRY_MESSAGE = "Your previous response was not valid JSON. Return ONLY a raw JSON object. No markdown fences. No explanation. No preamble. Just the JSON object with the fields: translated_script, confidence, confidence_reasoning, incompatible_elements, notes."


def _format_rag_context(chunks: list[dict]) -> str:
    """Format retrieved documentation chunks into a prompt section."""
    lines = ["## Retrieved Context (from Atlassian official documentation)"]
    for chunk in chunks:
        lines.append(f"--- source: {chunk['source']} ---")
        lines.append(chunk["content"])
        lines.append("")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Extract a JSON object from Gemini's response.

    Handles three common Gemini output patterns:
    - Raw JSON (ideal case)
    - JSON wrapped in ```json ... ``` fences
    - JSON preceded/followed by explanation text (preamble or postamble)
    """
    cleaned = text.strip()

    # Strip outermost markdown fences if present
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)
    cleaned = cleaned.strip()

    # Try direct parse first (fast path)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Gemini returned preamble/postamble around the JSON — find the object boundaries
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start != -1 and end != -1 and end > start:
        return json.loads(cleaned[start:end + 1])

    raise json.JSONDecodeError("No JSON object found in response", cleaned, 0)


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

    def _call_gemini(self, prompt: str) -> str:
        """Call Gemini and return the raw response text."""
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return response.text

    def _parse_translation(self, component: Component, prompt: str) -> TranslationResult:
        """Call Gemini, parse JSON response, retry once on failure, fallback on second failure."""
        try:
            raw = self._call_gemini(prompt)
            parsed = _extract_json(raw)
            return TranslationResult(**parsed)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[Agent] {component.component_id} parse attempt 1 failed ({type(e).__name__}): {e}")
        except Exception as e:
            print(f"[Agent] {component.component_id} attempt 1 error ({type(e).__name__}): {e}")
            return FALLBACK_RESULT

        # Retry with reinforcement message
        try:
            retry_prompt = prompt + "\n\n" + RETRY_MESSAGE
            raw = self._call_gemini(retry_prompt)
            parsed = _extract_json(raw)
            return TranslationResult(**parsed)
        except Exception as e:
            print(f"[Agent] {component.component_id} attempt 2 error ({type(e).__name__}): {e}")
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
        except Exception:
            return None

    def _store_translation(self, component_result: dict, result: TranslationResult) -> None:
        """Store a high-confidence translation in memory. Fails silently."""
        try:
            from rag import get_translation_memory

            memory = get_translation_memory()
            if memory is None:
                return

            memory.store(component_result, result)
        except Exception:
            pass

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
            }

        # Step 2 — RAG query + Gemini translation
        prompt = self.build_prompt(component)
        prompt = self._inject_rag_context(prompt, component)

        result = self._parse_translation(component, prompt)
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
        }

        # Step 3 — Store in translation memory if high confidence
        if status == "success" and result.confidence >= 0.85:
            self._store_translation(component_result, result)

        return component_result
