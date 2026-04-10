from pydantic import BaseModel, Field, model_validator
from typing import List, Optional
from enum import Enum

class CloudStatus(str, Enum):
    compatible = "compatible"
    partial = "partial"
    incompatible = "incompatible"

class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

class ComponentType(str, Enum):
    workflow_validator = "workflow_validator"
    script = "script"
    listener = "listener"
    post_function = "post_function"
    api_usage = "api_usage"

class Plugin(str, Enum):
    scriptrunner = "ScriptRunner"
    jsu = "JSU"
    misc = "MISC"
    native = "native"
    webhook = "Webhook"

class Location(BaseModel):
    workflow: Optional[str] = None
    transition: Optional[str] = None
    file_path: Optional[str] = None

class Compatibility(BaseModel):
    cloud_status: CloudStatus
    risk_level: RiskLevel

class Component(BaseModel):
    component_id: str
    component_type: ComponentType
    plugin: Plugin
    location: Location
    features_detected: List[str] = []
    compatibility: Compatibility
    recommended_action: str
    report_text: str
    original_script: Optional[str] = None

class AnalysisReport(BaseModel):
    analysis_id: str
    source_environment: str
    target_environment: str
    analysis_date: str
    components: List[Component]


class TranslationResult(BaseModel):
    translated_script: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    confidence_reasoning: str = ""
    incompatible_elements: List[str] = []
    notes: str = ""
    confidence_label: str = ""
    served_from_cache: bool = False
    cache_similarity: Optional[float] = None
    cache_warnings: List[str] = []
    reviewer_corrected: bool = False

    @model_validator(mode="after")
    def compute_confidence_label(self) -> "TranslationResult":
        if self.confidence >= 0.80:
            self.confidence_label = "high"
        elif self.confidence >= 0.50:
            self.confidence_label = "medium"
        else:
            self.confidence_label = "low"
        return self