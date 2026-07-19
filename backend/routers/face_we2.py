"""WE2 face inference simulator HTTP endpoint.

Mounted into the FastAPI app only when ``FACE_WE2_SIMULATOR_ENABLED=1``.
Exposes a ``face_rec_api``-compatible surface at ``/api/face/we2`` so that
the warehouse ``endpoint_client.infer()`` can be pointed at this same backend
(loopback) to enroll faces from photos without a physical WE2 device.

Response shape mirrors ``face_rec_api``:
    {
      "faces": [
        {
          "bbox": [x, y, w, h],
          "landmarks": [[x,y], ...5 points],
          "embedding": "<base64 of 128-D float32 LE raw bytes (512 bytes)>",
          "det_score": 0.93,
          "aligned_b64": "<base64 PNG>" | null,
          "quality": 0.87,
          "pose": {"yaw": ..., "pitch": ..., "roll": ...}
        }, ...
      ],
      "face_count": 1,
      "model_tag": "we2-mfnr6-128-v1",
      "backend": "we2-simulator",
      "processing_time_ms": 12.3
    }
"""

from __future__ import annotations

import base64
import io
import time
from typing import List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from face.we2 import MODEL_TAG, get_simulator

router = APIRouter(prefix="/api/face/we2", tags=["face-we2-simulator"])


class InferRequest(BaseModel):
    image_b64: str = Field(..., description="Base64-encoded image bytes (PNG/JPEG)")
    return_aligned: bool = Field(
        False, description="If true, include base64 PNG of the aligned 112x112 face"
    )


class PoseModel(BaseModel):
    yaw: float
    pitch: float
    roll: float


class FaceResult(BaseModel):
    bbox: List[float]  # [x, y, w, h] in original image space
    landmarks: List[List[float]]  # 5 (x,y) points
    embedding: str  # base64 of 128-D float32 LE raw bytes (512 bytes), no L2 normalize
    det_score: float
    aligned_b64: Optional[str] = None
    quality: float
    pose: PoseModel


class InferResponse(BaseModel):
    faces: List[FaceResult]
    face_count: int
    model_tag: str
    backend: str = "we2-simulator"
    processing_time_ms: float


class HealthResponse(BaseModel):
    status: str
    backend: str
    model_tag: str
    capabilities: List[str]
    embedding_dim: int
    embedding_dtype: str


def _decode_image(image_b64: str) -> Image.Image:
    try:
        raw = base64.b64decode(image_b64, validate=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid base64: {e}")
    if not raw:
        raise HTTPException(status_code=400, detail="empty image payload")
    try:
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"unable to decode image: {e}")


def _encode_aligned_png(aligned: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(aligned).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


@router.post("/infer", response_model=InferResponse)
def we2_infer(req: InferRequest) -> InferResponse:
    """Run the WE2 simulator on a base64 image; return face_rec_api shape."""
    image = _decode_image(req.image_b64)

    sim = get_simulator()
    t0 = time.perf_counter()
    try:
        result = sim.infer(image)
    except FileNotFoundError as e:
        # Model files missing — surface as 503 so the client can retry / log
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # pragma: no cover — defensive
        raise HTTPException(status_code=500, detail=f"inference failed: {e}")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    faces: List[FaceResult] = []
    for f in result["faces"]:
        aligned_b64 = (
            _encode_aligned_png(f["aligned_face"]) if req.return_aligned else None
        )
        faces.append(
            FaceResult(
                bbox=f["bbox"],
                landmarks=f["landmarks"],
                embedding=base64.b64encode(f["embedding_bytes"]).decode("ascii"),
                det_score=f["det_score"],
                aligned_b64=aligned_b64,
                quality=f["quality"],
                pose=PoseModel(**f["pose"]),
            )
        )

    return InferResponse(
        faces=faces,
        face_count=len(faces),
        model_tag=result["model_tag"],
        backend="we2-simulator",
        processing_time_ms=round(elapsed_ms, 3),
    )


@router.get("/health", response_model=HealthResponse)
def we2_health() -> HealthResponse:
    """Health probe. Touches the simulator only to confirm models exist."""
    sim = get_simulator()
    # Probe model paths without forcing tflite load on every health call
    for path in (sim.scrfd_path, sim.mfn_path):
        if not path.exists():
            raise HTTPException(
                status_code=503,
                detail=f"model missing: {path.name}",
            )
    return HealthResponse(
        status="healthy",
        backend="we2-simulator",
        model_tag=MODEL_TAG,
        capabilities=["detect", "embed"],
        embedding_dim=128,
        embedding_dtype="float32",
    )
