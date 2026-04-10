from app.agents.base_agent import BaseAgent
from app.models import Component

class AutomationAgent(BaseAgent):
    def build_prompt(self, component: Component) -> str:
        return f"""
You are an expert Jira migration engineer specializing in Jira Automation rules.

Your task is to translate a Jira Data Center automation component to Jira Cloud Automation format.

## Component Info
- Type: {component.component_type.value}
- Location: workflow '{component.location.workflow}', transition '{component.location.transition}'
- Risk level: {component.compatibility.risk_level.value}
- Recommended action: {component.recommended_action}

## Features detected
{", ".join(component.features_detected) if component.features_detected else "none"}

## Analysis notes
{component.report_text}

## Original script
{component.original_script or "No script provided"}

## Instructions
- Translate this to a valid Jira Cloud Automation rule in JSON format
- Use Jira Cloud Automation trigger/condition/action structure
- Replace any DC-specific triggers or actions with their Cloud equivalents
- The translated_script field must contain valid JSON representing the Jira Cloud Automation rule
"""
