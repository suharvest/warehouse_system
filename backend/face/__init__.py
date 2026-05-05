"""Face recognition + permission gating (Phase 1, MCP-only).

Public surface used by app.py, MCP, and tests.
"""
from .models import Decision, FaceConfig, FaceRule, Match
from .orchestrator import enroll_face, verify_mcp_face

__all__ = [
    "Decision",
    "FaceConfig",
    "FaceRule",
    "Match",
    "enroll_face",
    "verify_mcp_face",
]
