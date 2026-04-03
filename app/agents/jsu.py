from app.agents.base_agent import BaseAgent, JSON_OUTPUT_INSTRUCTIONS
from app.models import Component

class JSUAgent(BaseAgent):
    def build_prompt(self, component: Component) -> str:
        return f"""
You are an expert Jira migration engineer specializing in JSU (JIRA Suite Utilities) migrations.

Your task is to translate a JSU workflow component from Jira Data Center to Jira Cloud.

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
- JSU has limited Cloud support — translate to ScriptRunner Cloud Groovy where possible
- If the JSU feature has a native Jira Cloud equivalent, use that instead
- Use accountId for all user references
- If migration is not possible, return a Groovy comment block explaining what needs manual reconfiguration
- The translated_script field in your JSON response must contain valid Groovy code

{JSON_OUTPUT_INSTRUCTIONS}
"""
