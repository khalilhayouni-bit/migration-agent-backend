from app.models import AnalysisReport, Plugin
from app.agents.scriptrunner import ScriptRunnerAgent
from app.agents.jsu import JSUAgent
from app.agents.automation import AutomationAgent
from app.agents.misc import MiscAgent

from app.agents.webhook import WebhookAgent

AGENT_MAP = {
    Plugin.scriptrunner: ScriptRunnerAgent(),
    Plugin.jsu: JSUAgent(),
    Plugin.native: AutomationAgent(),
    Plugin.webhook: WebhookAgent(),
    Plugin.misc: MiscAgent(),
}

def route_components(report: AnalysisReport) -> list[dict]:
    results = []

    for component in report.components:
        agent = AGENT_MAP.get(component.plugin)

        if agent is None:
            agent = MiscAgent()

        print(f"[Router] Processing component '{component.component_id}' → {component.plugin.value}")

        result = agent.translate(component)
        results.append(result)

    return results
from typing import Generator

def route_components_stream(report: AnalysisReport) -> Generator[dict, None, None]:
    for index, component in enumerate(report.components):
        agent = AGENT_MAP.get(component.plugin) or MiscAgent()

        print(f"[Router] Processing component '{component.component_id}' → {component.plugin.value}")

        # Signal to frontend: this component is now running
        yield {
            "type": "agent_start",
            "index": index,
            "component_id": component.component_id,
            "plugin": component.plugin.value,
        }

        result = agent.translate(component)

        # Signal to frontend: this component is done
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