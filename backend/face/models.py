"""Face recognition DTOs."""
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class FaceConfig:
    tenant_id: int
    enabled: bool = False
    mode: Optional[str] = None  # 'local' | 'lan'
    endpoint: Optional[str] = None
    auth_token: Optional[str] = None
    embedding_model_tag: Optional[str] = None
    min_confidence: float = 0.65


@dataclass
class FaceRule:
    id: int
    tenant_id: int
    warehouse_id: Optional[int]
    operation: str
    require_face: bool = False
    allowed_subject_ids: List[int] = field(default_factory=list)
    min_confidence_override: Optional[float] = None


@dataclass
class Match:
    enrollment_id: int
    subject_id: int
    confidence: float


@dataclass
class Decision:
    """Result of verify_mcp_face.

    status:
      - 'pass'    : verified
      - 'deny'    : require_face=True but check failed
      - 'skipped' : feature disabled or no rule requires face
    """
    status: str
    failure_reason: Optional[str] = None
    confidence: Optional[float] = None
    matched_subject_id: Optional[int] = None
