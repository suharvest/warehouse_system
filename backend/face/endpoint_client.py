"""Async HTTP client for face recognition endpoints.

The tenant-configured endpoint is expected to follow the unified
``face_rec_api`` contract (Hailo / Jetson / RKNN / WE2-PC-simulator):

  POST /infer   {image_b64}
       -> {faces: [{embedding (b64 float32), det_score, bbox, ...}],
           face_count, model_tag, backend, processing_time_ms}
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


def _headers(auth_token: Optional[str]) -> dict:
    h = {"Content-Type": "application/json"}
    if auth_token:
        h["Authorization"] = f"Bearer {auth_token}"
    return h


class FaceEndpointError(Exception):
    """Raised when the face endpoint is unreachable or returns an error."""


async def infer(cfg: FaceConfig, image_b64: str) -> dict:
    """Send an image to the endpoint, get back an embedding.

    Returns: {embedding: bytes, model_tag: str}
    Raises FaceEndpointError on connection / protocol failure.
    """
    if not cfg.endpoint:
        raise FaceEndpointError("endpoint_not_configured")
    url = cfg.endpoint.rstrip("/") + "/infer"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(
                url,
                json={"image_b64": image_b64},
                headers=_headers(cfg.auth_token),
            )
            if resp.status_code >= 400:
                raise FaceEndpointError(f"infer_http_{resp.status_code}")
            data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("face infer failed: %s", e)
        raise FaceEndpointError("endpoint_unreachable") from e

    model_tag = data.get("model_tag") or cfg.embedding_model_tag or "unknown"
    faces = data.get("faces") or []
    if not isinstance(faces, list) or not faces:
        raise FaceEndpointError("no_face_detected")
    # Pick highest-det-score face; fall back to first if score absent.
    best = max(faces, key=lambda f: float(f.get("det_score") or 0.0))
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
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers=_headers(auth_token))
            if resp.status_code >= 400:
                raise FaceEndpointError(f"health_http_{resp.status_code}")
            return resp.json()
    except httpx.HTTPError as e:
        raise FaceEndpointError("endpoint_unreachable") from e
