"""Async HTTP client for face recognition endpoints.

Each tenant configures an endpoint that exposes:
  POST /infer   {image_b64} -> {embedding: <base64 bytes>, model_tag}
  POST /capture {} -> {image_b64, ts}
  GET  /health  -> {status, model_tag, capabilities}

The actual implementation runs on the operator's device (Hello-style
local daemon, Jetson edge box, or custom). This client is intentionally
transport-only — no business logic.
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

    emb_b64 = data.get("embedding")
    model_tag = data.get("model_tag") or cfg.embedding_model_tag or "unknown"
    if not emb_b64:
        raise FaceEndpointError("infer_no_embedding")
    try:
        emb_bytes = base64.b64decode(emb_b64)
    except Exception as e:
        raise FaceEndpointError("infer_bad_embedding") from e
    return {"embedding": emb_bytes, "model_tag": model_tag}


async def capture(cfg: FaceConfig) -> dict:
    """Ask the endpoint to capture a fresh face snapshot.

    Returns: {image_b64: str, ts: str}
    Raises FaceEndpointError on failure.
    """
    if not cfg.endpoint:
        raise FaceEndpointError("endpoint_not_configured")
    url = cfg.endpoint.rstrip("/") + "/capture"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.post(url, json={}, headers=_headers(cfg.auth_token))
            if resp.status_code >= 400:
                raise FaceEndpointError(f"capture_http_{resp.status_code}")
            data = resp.json()
    except httpx.HTTPError as e:
        logger.warning("face capture failed: %s", e)
        raise FaceEndpointError("endpoint_unreachable") from e

    if not data.get("image_b64"):
        raise FaceEndpointError("capture_no_image")
    return data


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
