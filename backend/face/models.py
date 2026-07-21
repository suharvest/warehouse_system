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
    min_confidence: float = 0.45
    # 人脸验证频率（与 mode 正交，只控制会话缓存）：
    #   'always'  — 每次操作都现场验证（默认）
    #   'session' — 同会话首验通过后免验（session_cached）
    # 注：旧 verify_mode 列已 deprecated，验证链路只看 mode（local=设备拉身份 /
    # lan=端点重比对），代码不再读 verify_mode。
    verify_frequency: str = "always"


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
    # 识别到的人员姓名（face_subjects.name 快照），仅 pass 时填充；
    # 供出入库记录在操作人后追加显示 "operator (姓名)"。
    matched_subject_name: Optional[str] = None
