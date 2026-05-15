import asyncio
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
from typing import Generator

from app.models import AnalysisReport, Plugin
from app.agents.scriptrunner import ScriptRunnerAgent
from app.agents.jsu import JSUAgent
from app.agents.automation import AutomationAgent
from app.agents.misc import MiscAgent
from app.agents.webhook import WebhookAgent
from app.services import dedup

KEEPALIVE_INTERVAL_SECONDS = 15

AGENT_MAP = {
    Plugin.scriptrunner: ScriptRunnerAgent(),
    Plugin.jsu: JSUAgent(),
    Plugin.native: AutomationAgent(),
    Plugin.webhook: WebhookAgent(),
    Plugin.misc: MiscAgent(),
}

_misc_fallback = MiscAgent()


async def _translate_with_dedup(agent, component) -> dict:
    """Translate a component with deduplication — if the same component_id is
    already in-flight, await the existing result instead of starting a duplicate."""
    cid = component.component_id
    is_new, future = await dedup.get_or_register(cid)

    if not is_new:
        print(f"[Router] Dedup: '{cid}' already in-flight, waiting for existing result")
        return await future

    try:
        result = await agent.async_translate(component)
        await dedup.complete(cid, result)
        return result
    except Exception as exc:
        await dedup.fail(cid, exc)
        raise


async def route_components(report: AnalysisReport) -> list[dict]:
    tasks = []
    for component in report.components:
        agent = AGENT_MAP.get(component.plugin, _misc_fallback)
        print(f"[Router] Queuing component '{component.component_id}' -> {component.plugin.value}")
        tasks.append(_translate_with_dedup(agent, component))

    results = await asyncio.gather(*tasks)
    return list(results)

def route_components_stream(report: AnalysisReport) -> Generator[dict, None, None]:
    """Yield events as each component is translated.

    A single component can take well over a minute when Gemini hits 429
    backoff + the reviewer step also runs, so the work is dispatched to a
    background thread and the generator yields `keepalive` events every
    KEEPALIVE_INTERVAL_SECONDS while waiting. The SSE layer translates
    those into comment lines that keep the HTTP connection alive without
    showing up as data events in the browser.
    """
    with ThreadPoolExecutor(max_workers=1) as executor:
        for index, component in enumerate(report.components):
            agent = AGENT_MAP.get(component.plugin) or MiscAgent()

            print(f"[Router] Processing component '{component.component_id}' -> {component.plugin.value}")

            yield {
                "type": "agent_start",
                "index": index,
                "component_id": component.component_id,
                "plugin": component.plugin.value,
            }

            future = executor.submit(agent.translate, component)
            while True:
                try:
                    result = future.result(timeout=KEEPALIVE_INTERVAL_SECONDS)
                    break
                except concurrent.futures.TimeoutError:
                    yield {"type": "keepalive", "index": index, "component_id": component.component_id}

            yield {
                "type": "agent_done",
                "index": index,
                "component_id": component.component_id,
                "plugin": component.plugin.value,
                "outputExt": result.get("output_ext", "flagged"),
                "status": result.get("status", "done"),
                "confidence": result.get("confidence", 0.0),
                "confidence_label": result.get("confidence_label", "low"),
                "confidence_reasoning": result.get("confidence_reasoning", ""),
                "served_from_cache": result.get("served_from_cache", False),
                "cache_similarity": result.get("cache_similarity"),
                "cache_warnings": result.get("cache_warnings", []),
                "result": result,  # stashed by migration.py for validation step
            }