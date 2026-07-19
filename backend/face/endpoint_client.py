"""Async HTTP client for face recognition endpoints.

The tenant-configured endpoint is expected to follow the unified
``face_rec_api`` contract (Hailo / Jetson / RKNN / WE2-PC-simulator):

  POST /infer   {image_b64}
       -> {faces: [{embedding (b64 float32), det_score, bbox,
                    live?, liveness_score?, ...}],
           face_count, model_tag, backend, processing_time_ms}

When the endpoint runs passive liveness in reject mode, a spoofed face
comes back with ``live=false`` and ``embedding=null`` — surfaced here as
``FaceEndpointError("spoof")`` so the audit log records the real reason.
  GET  /health  -> {status, model_tag, backend, capabilities, ...}

Stateless: there is no /capture. Upstream (frontend / MCP host) is
responsible for providing the image. When multiple faces are present
this client picks the one with the highest ``det_score`` (matches the
service-side ``MULTIPLE_FACES_STRATEGY=largest`` default — det score
correlates with face size).
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from .models import FaceConfig

logger = logging.getLogger("warehouse.face")

DEFAULT_TIMEOUT = 10.0

# 进程内 WE2 模拟器（mode=local）的 embedding 模型标签。设备端（Himax WE2 NPU）
# 与本模拟器同模型，routers/mcp_admin.py 的 DEVICE_FACE_MODEL_TAG 也须与此一致。
LOCAL_MODEL_TAG = "we2-mfnr6-128-v1"


def _headers(auth_token: Optional[str]) -> dict:
    h = {"Content-Type": "application/json"}
    if auth_token:
        h["Authorization"] = f"Bearer {auth_token}"
    return h


class FaceEndpointError(Exception):
    """Raised when the face endpoint is unreachable or returns an error."""


def _infer_local(image_b64: str) -> dict:
    """In-process inference via the bundled WE2 simulator (mode=local).

    No HTTP, no endpoint required. Decodes the base64 image, runs the
    process-wide ``WE2Simulator`` singleton, and returns the same
    ``{embedding: bytes, model_tag: str}`` shape as the HTTP path. Picks the
    highest ``det_score`` face to match the remote-endpoint selection rule.
    """
    import base64 as _b64
    import io

    from PIL import Image

    try:
        raw = _b64.b64decode(image_b64, validate=False)
    except Exception as e:
        raise FaceEndpointError("infer_bad_image") from e
    if not raw:
        raise FaceEndpointError("infer_bad_image")
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:
        raise FaceEndpointError("infer_bad_image") from e

    try:
        from face.we2 import get_simulator
    except Exception as e:  # we2-sim extra not installed
        logger.warning("local face simulator unavailable: %s", e)
        raise FaceEndpointError("local_simulator_unavailable") from e

    try:
        result = get_simulator().infer(image)
    except FileNotFoundError as e:
        raise FaceEndpointError("local_model_missing") from e
    except Exception as e:
        logger.warning("local face infer failed: %s", e)
        raise FaceEndpointError("local_infer_failed") from e

    faces = result.get("faces") or []
    if not faces:
        raise FaceEndpointError("no_face_detected")
    best = max(faces, key=lambda f: float(f.get("det_score") or 0.0))
    emb_bytes = best.get("embedding_bytes")
    if not emb_bytes:
        raise FaceEndpointError("infer_no_embedding")
    model_tag = result.get("model_tag") or LOCAL_MODEL_TAG
    return {"embedding": emb_bytes, "model_tag": model_tag}


def _parse_json_response(resp: "httpx.Response", prefix: str) -> dict:
    """协议校验统一收口：非 2xx、非法 JSON、非 dict 都归为 FaceEndpointError，
    避免解析异常逃出 FaceEndpointError 错误处理变 500。"""
    if resp.status_code >= 400:
        raise FaceEndpointError(f"{prefix}_http_{resp.status_code}")
    try:
        data = resp.json()
    except ValueError as e:
        raise FaceEndpointError(f"{prefix}_bad_response") from e
    if not isinstance(data, dict):
        raise FaceEndpointError(f"{prefix}_bad_response")
    return data


async def infer(cfg: FaceConfig, image_b64: str) -> dict:
    """Send an image to the endpoint, get back an embedding.

    Returns: {embedding: bytes, model_tag: str}
    Raises FaceEndpointError on connection / protocol failure.

    ``cfg.mode == "local"`` runs the bundled WE2 simulator in-process (no
    endpoint needed). Any other mode (``lan``) uses the external HTTP
    endpoint per the face_rec_api contract.
    """
    if cfg.mode == "local":
        return _infer_local(image_b64)
    if not cfg.endpoint:
        raise FaceEndpointError("endpoint_not_configured")
    url = cfg.endpoint.rstrip("/") + "/infer"
    try:
        # trust_env=False: LAN face endpoints must be reached directly —
        # env/system proxies (macOS scutil proxy, Clash, ...) cannot route
        # Tailscale/LAN ranges and turn every call into a 502 (same fix as
        # device_pull.py).
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, trust_env=False
        ) as client:
            resp = await client.post(
                url,
                json={"image_b64": image_b64},
                headers=_headers(cfg.auth_token),
            )
            data = _parse_json_response(resp, "infer")
    except httpx.HTTPError as e:
        logger.warning("face infer failed: %s", e)
        raise FaceEndpointError("endpoint_unreachable") from e

    model_tag = data.get("model_tag") or cfg.embedding_model_tag or "unknown"
    faces = data.get("faces") or []
    if not isinstance(faces, list):
        raise FaceEndpointError("infer_bad_response")
    faces = [f for f in faces if isinstance(f, dict)]
    if not faces:
        raise FaceEndpointError("no_face_detected")
    # Pick highest-det-score face; fall back to first if score absent.
    best = max(faces, key=lambda f: float(f.get("det_score") or 0.0))
    # face_rec_api passive liveness: a spoofed face (print / screen replay)
    # comes back with live=false and no embedding. Surface it as its own
    # reason code instead of the misleading "infer_no_embedding".
    if best.get("live") is False:
        raise FaceEndpointError("spoof")
    emb_b64 = best.get("embedding")
    if not emb_b64:
        raise FaceEndpointError("infer_no_embedding")
    try:
        emb_bytes = base64.b64decode(emb_b64)
    except Exception as e:
        raise FaceEndpointError("infer_bad_embedding") from e
    return {"embedding": emb_bytes, "model_tag": model_tag}


async def health(endpoint: str, auth_token: Optional[str] = None) -> dict:
    """Probe an endpoint's /health for the test-connection management API."""
    if not endpoint:
        raise FaceEndpointError("endpoint_not_configured")
    url = endpoint.rstrip("/") + "/health"
    try:
        # trust_env=False: see infer() — never proxy LAN endpoint probes.
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, trust_env=False
        ) as client:
            resp = await client.get(url, headers=_headers(auth_token))
            return _parse_json_response(resp, "health")
    except httpx.HTTPError as e:
        raise FaceEndpointError("endpoint_unreachable") from e
