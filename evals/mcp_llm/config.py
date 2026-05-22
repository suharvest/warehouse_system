"""RunConfig / ProviderConfig / CaseFilter dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ProviderConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.3
    timeout: float = 60.0
    tool_choice: str = "auto"
    stream: bool = True


@dataclass
class CaseFilter:
    filter_class: Optional[list] = None
    filter_id: Optional[list] = None
    limit: Optional[int] = None


@dataclass
class RunConfig:
    provider: ProviderConfig
    cases_path: Path
    seed_path: Path
    prompt_paths: list  # list[Path]
    k: int = 1
    concurrency: int = 4
    write_concurrency: int = 1
    backend_port_base: int = 12450
    mcp_script: Path = Path("mcp/warehouse_mcp.py")
    output_dir: Optional[Path] = None
    keep_db_on_fail: bool = False
    fail_fast: bool = False
    dry_run: bool = False
    case_filter: CaseFilter = field(default_factory=CaseFilter)
    run_id: str = ""
