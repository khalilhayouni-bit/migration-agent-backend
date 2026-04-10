from app.agents.base_agent import BaseAgent
from app.models import Component

class ScriptRunnerAgent(BaseAgent):
    def build_prompt(self, component: Component) -> str:
        return f"""
You are an expert Jira migration engineer specializing in ScriptRunner Groovy scripts.

Your task is to translate a Jira Data Center ScriptRunner script to a Jira Cloud compatible version.

## Component Info
- Type: {component.component_type.value}
- Location: workflow '{component.location.workflow}', transition '{component.location.transition}'
- Risk level: {component.compatibility.risk_level.value}
- Recommended action: {component.recommended_action}

## Features detected in original script
{", ".join(component.features_detected) if component.features_detected else "none"}

## Analysis notes
{component.report_text}

## Original script
{component.original_script or "No script provided"}

## Instructions
- Rewrite this script to be fully compatible with Jira Cloud ScriptRunner
- Replace any DC-only APIs (ComponentAccessor, IssueManager, etc.) with their Cloud REST API equivalents
- Use accountId instead of username or userkey for user identification
- Use Jira Cloud REST API v3 endpoints where needed
- If a feature is completely unsupported in Cloud, add a comment explaining why and suggest an alternative
- The translated_script field must contain valid Groovy code
"""
