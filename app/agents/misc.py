from app.agents.base_agent import BaseAgent, JSON_OUTPUT_INSTRUCTIONS
from app.models import Component

class MiscAgent(BaseAgent):
    def build_prompt(self, component: Component) -> str:
        return f"""
You are an expert Jira migration engineer reviewing an unsupported or unknown component.

## Component Info
- Type: {component.component_type.value}
- Plugin: {component.plugin.value}
- Risk level: {component.compatibility.risk_level.value}
- Recommended action: {component.recommended_action}

## Features detected
{", ".join(component.features_detected) if component.features_detected else "none"}

## Analysis notes
{component.report_text}

## Original script
{component.original_script or "No script provided"}

## Instructions
- This component could not be automatically translated
- The translated_script field should contain a detailed Groovy comment block (/* ... */) explaining:
  1. Why this component cannot be auto-migrated
  2. What manual steps are needed
  3. Any Cloud alternatives available
- Set confidence low since this is a fallback/unsupported component

{JSON_OUTPUT_INSTRUCTIONS}
"""
