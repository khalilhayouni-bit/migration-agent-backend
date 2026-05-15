from app.agents.base_agent import BaseAgent
from app.models import Component

class WebhookAgent(BaseAgent):
    def build_prompt(self, component: Component) -> str:
        return f"""
You are an expert Jira migration engineer specializing in webhook integrations \
between Jira Data Center and Jira Cloud.

Your task is to translate a Jira Data Center webhook component to its \
Jira Cloud equivalent and return a structured JSON response.

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

## Translation rules

Determine which direction the webhook flows, then apply the correct pattern:

**Outbound (DC fires HTTP POST to external system):**
- Translate to a Python handler using the `requests` library
- Replace Groovy HTTPBuilder / openConnection calls with `requests.post()`
- Replace DC username references with accountId
- Preserve the payload structure, updating any DC-specific field names to 
  their Jira Cloud equivalents (e.g. `reporter.name` → `reporter.accountId`)
- Add a `X-Atlassian-Token` or webhook secret verification comment where 
  authentication was present in the original

**Inbound (DC listener reacts to Jira events):**
- Translate to a Python Flask/FastAPI receiver function
- Map the DC event type to the equivalent Jira Cloud webhook event 
  (e.g. `jira:issue_created`, `jira:issue_updated`)
- Use Jira Cloud REST API v3 for any follow-up API calls
- Use accountId for all user references, never username or userkey
- Include the correct Cloud webhook payload field paths 
  (e.g. `data["issue"]["fields"]["assignee"]["accountId"]`)

**General rules:**
- If the original script is too minimal to determine intent, write a 
  well-commented Python stub that explains what the webhook should do 
  and what Cloud APIs to call, with TODO markers
- Never leave DC-only imports (groovyx, com.atlassian) in the output
- Always produce syntactically valid Python 3.9+ code
- Escape all quotes and special characters properly inside the 
  translated_script string value

## Output format
You must return a JSON object with these exact fields:
- translated_script: a string containing the complete Python code 
  (escape newlines as \\n, escape quotes as needed for valid JSON)
- confidence: float 0.0-1.0
- confidence_reasoning: one sentence explaining the score
- incompatible_elements: list of strings naming any DC features with 
  no Cloud equivalent
- notes: any additional migration notes for the engineer
"""