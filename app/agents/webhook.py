from app.agents.base_agent import BaseAgent
from app.models import Component

class WebhookAgent(BaseAgent):
    def build_prompt(self, component: Component) -> str:
        return f"""
You are an expert Jira migration engineer specializing in webhook integrations.

Your task is to translate a Jira Data Center webhook to a Jira Cloud compatible Python handler.

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
- Rewrite this as a Python webhook handler compatible with Jira Cloud
- Use Jira Cloud REST API v3 for any API calls
- Use accountId for user references
- The translated_script field must contain valid Python code
"""
