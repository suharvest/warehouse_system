"""WE2 face embedding simulator — bit-exact host mirror of the WE2 NPU pipeline.

The on-device WE2 firmware runs (a) SCRFD-500M for face detection + 5 keypoints,
then (b) MobileFaceNet-128D for embedding, both as INT8 TFLite. This module
replays that pipeline on the host using ``ai-edge-litert`` with
``OpResolverType.BUILTIN_REF`` — which is byte-for-byte bit-exact vs TF 2.19
(verified at handoff via SHA256).

Inputs:  RGB image (any size) decoded by PIL.
Outputs: 128 raw INT8 bytes per detected face (no dequantize, no L2 normalize)
         plus bbox / landmarks / det_score / aligned crop, packed in the same
         shape as the ``face_rec_api`` ``/infer`` response.

This module deliberately:
  * Does NOT depend on TensorFlow or OpenCV.
  * Does NOT L2-normalize or dequantize the embedding — the warehouse matcher
    consumes the raw INT8 bytes (cosine over INT8 vectors).
  * Loads models lazily on the first ``infer()`` call, behind a process-wide
    ``threading.Lock``; subsequent calls reuse the cached interpreters.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, List, Optional

import numpy as np
from PIL import Image

MODEL_TAG = "we2-mfn128-v1"

# ---------------------------------------------------------------------------
# Constants — mirror common_config.h + face_alignment.h on the device
# ---------------------------------------------------------------------------

FD_INPUT_W = 160
FD_INPUT_H = 160

EMB_INPUT_W = 112
EMB_INPUT_H = 112
EMB_OUTPUT_DIM = 128

SCRFD_NUM_STRIDES = 3
SCRFD_NUM_ANCHORS = 2
SCRFD_NUM_LANDMARKS = 5
STRIDES = (8, 16, 32)

FACE_CONF_THRESHOLD = 0.70
FACE_NMS_THRESHOLD = 0.40
MIN_FACE_SIZE = 40
CENTER_DIST_THRESH_RATIO = 0.3
MAX_FACE_RATIO = 0.6

# ArcFace canonical 112x112 reference landmarks (face_alignment.c:21-27)
REFERENCE_LANDMARKS = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

LM_LEFT_EYE = 0
LM_RIGHT_EYE = 1
LM_NOSE = 2
LM_LEFT_MOUTH = 3
LM_RIGHT_MOUTH = 4

_THIS_DIR = Path(__file__).resolve().parent
_MODELS_DIR = _THIS_DIR / "models"

SCRFD_MODEL_PATH = _MODELS_DIR / "scrfd_500m_kps.int8.tflite"
MFN_MODEL_PATH = _MODELS_DIR / "mfn128_distilled.int8.tflite"


# ---------------------------------------------------------------------------
# Pure functions (no model state) — adapted from compute_embedding.py
# ---------------------------------------------------------------------------

def _round_half_away_from_zero(x: np.ndarray) -> np.ndarray:
    return np.where(x >= 0.0, np.floor(x + 0.5), np.ceil(x - 0.5))


def _quantize_uint8_to_int8(rgb: np.ndarray, zero_point: int) -> np.ndarray:
    """Mirror firmware: dst[i] = clamp(src[i] + zp, -128, 127)."""
    val = rgb.astype(np.int32) + zero_point
    return np.clip(val, -128, 127).astype(np.int8)


def _dequantize_int8(data: np.ndarray, scale: float, zero_point: int) -> np.ndarray:
    return (data.astype(np.float32) - zero_point) * scale


def _quantize_embedding_input_rgb(
    aligned_face: np.ndarray, input_scale: float, input_zp: int
) -> np.ndarray:
    """Mirror firmware: real = pixel/127.5 - 1; q = round(real/scale + zp)."""
    if input_zp == -1 and abs(float(input_scale) - (1.0 / 127.5)) < 1e-5:
        a = aligned_face.astype(np.int32)
        q = np.where(a > 128, a - 128, a - 129)
        return np.clip(q, -128, 127).astype(np.int8)

    if input_scale <= 0:
        q = aligned_face.astype(np.int32) - 128
    else:
        real = aligned_face.astype(np.float32) / 127.5 - 1.0
        q = _round_half_away_from_zero(real / float(input_scale) + int(input_zp))
    return np.clip(q, -128, 127).astype(np.int8)


def _image_to_bgr_planar(pil_image: Image.Image) -> np.ndarray:
    """Convert PIL RGB to (3, H, W) BGR planar (camera output format)."""
    rgb = pil_image.convert("RGB")
    arr = np.asarray(rgb, dtype=np.uint8)  # (H, W, 3) RGB interleaved
    b = arr[:, :, 2]
    g = arr[:, :, 1]
    r = arr[:, :, 0]
    return np.stack([b, g, r], axis=0)


def _resize_bgr_planar_to_rgb_interleaved(
    bgr_planar: np.ndarray, dst_w: int, dst_h: int
) -> np.ndarray:
    """Bilinear resize each plane via PIL, then re-interleave as RGB."""
    b, g, r = bgr_planar[0], bgr_planar[1], bgr_planar[2]
    b_r = np.asarray(
        Image.fromarray(b, mode="L").resize((dst_w, dst_h), Image.BILINEAR),
        dtype=np.uint8,
    )
    g_r = np.asarray(
        Image.fromarray(g, mode="L").resize((dst_w, dst_h), Image.BILINEAR),
        dtype=np.uint8,
    )
    r_r = np.asarray(
        Image.fromarray(r, mode="L").resize((dst_w, dst_h), Image.BILINEAR),
        dtype=np.uint8,
    )
    return np.stack([r_r, g_r, b_r], axis=-1)  # RGB interleaved


def _compute_iou(bbox_a, bbox_b) -> float:
    ax, ay, aw, ah = bbox_a
    bx, by, bw, bh = bbox_b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter = inter_w * inter_h
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def _is_same_face_by_center(det_a, det_b, thresh_ratio) -> bool:
    ax, ay, aw, ah = det_a["bbox"]
    bx, by, bw, bh = det_b["bbox"]
    cx_a = ax + aw / 2
    cy_a = ay + ah / 2
    cx_b = bx + bw / 2
    cy_b = by + bh / 2
    dist = float(np.hypot(cx_a - cx_b, cy_a - cy_b))
    ref_size = min(min(aw, ah), min(bw, bh))
    return dist < ref_size * thresh_ratio


def _scrfd_decode_and_nms(
    score_tensors,
    bbox_tensors,
    kps_tensors,
    score_scales,
    score_zps,
    bbox_scales,
    bbox_zps,
    kps_scales,
    kps_zps,
    input_w: int,
    input_h: int,
    img_w: int,
    img_h: int,
    score_thresh: float,
    nms_thresh: float,
) -> List[dict]:
    """Exact replica of on-device scrfd_detect + NMS + cross-stride suppress."""
    scale_x = img_w / input_w
    scale_y = img_h / input_h

    all_dets = []

    for s in range(SCRFD_NUM_STRIDES):
        stride = STRIDES[s]
        grid_h = input_h // stride
        grid_w = input_w // stride

        score_f = _dequantize_int8(
            score_tensors[s], score_scales[s], score_zps[s]
        ).flatten()
        bbox_f = _dequantize_int8(bbox_tensors[s], bbox_scales[s], bbox_zps[s])
        kps_f = _dequantize_int8(kps_tensors[s], kps_scales[s], kps_zps[s])

        # Reshape bbox/kps to [N, C]
        bbox_f = bbox_f.reshape(-1, 4)
        kps_f = kps_f.reshape(-1, 10)

        for h in range(grid_h):
            for w in range(grid_w):
                for a in range(SCRFD_NUM_ANCHORS):
                    row = (h * grid_w + w) * SCRFD_NUM_ANCHORS + a
                    score = min(1.0, max(0.0, float(score_f[row])))
                    if score < score_thresh:
                        continue

                    d_left = bbox_f[row, 0]
                    d_top = bbox_f[row, 1]
                    d_right = bbox_f[row, 2]
                    d_bottom = bbox_f[row, 3]

                    cx = w * stride
                    cy = h * stride
                    x1 = cx - d_left * stride
                    y1 = cy - d_top * stride
                    x2 = cx + d_right * stride
                    y2 = cy + d_bottom * stride

                    x1 = max(0.0, min(float(input_w), x1))
                    y1 = max(0.0, min(float(input_h), y1))
                    x2 = max(0.0, min(float(input_w), x2))
                    y2 = max(0.0, min(float(input_h), y2))

                    orig_x1 = max(0.0, min(float(img_w), x1 * scale_x))
                    orig_y1 = max(0.0, min(float(img_h), y1 * scale_y))
                    orig_x2 = max(0.0, min(float(img_w), x2 * scale_x))
                    orig_y2 = max(0.0, min(float(img_h), y2 * scale_y))

                    landmarks = []
                    for k in range(SCRFD_NUM_LANDMARKS):
                        kp_dx = kps_f[row, k * 2]
                        kp_dy = kps_f[row, k * 2 + 1]
                        lm_x = (w * stride + kp_dx * stride) * scale_x
                        lm_y = (h * stride + kp_dy * stride) * scale_y
                        landmarks.append((float(lm_x), float(lm_y)))

                    all_dets.append(
                        (
                            s,
                            {
                                "bbox": (
                                    float(orig_x1),
                                    float(orig_y1),
                                    float(orig_x2 - orig_x1),
                                    float(orig_y2 - orig_y1),
                                ),
                                "score": score,
                                "landmarks": landmarks,
                                "stride_idx": s,
                            },
                        )
                    )

    # Intra-stride NMS
    for s in range(SCRFD_NUM_STRIDES):
        stride_dets = [d for si, d in all_dets if si == s and d["score"] > 0]
        stride_dets.sort(key=lambda d: d["score"], reverse=True)
        for i in range(len(stride_dets)):
            if stride_dets[i]["score"] <= 0:
                continue
            for j in range(i + 1, len(stride_dets)):
                if stride_dets[j]["score"] <= 0:
                    continue
                if _compute_iou(stride_dets[i]["bbox"], stride_dets[j]["bbox"]) > nms_thresh:
                    stride_dets[j]["score"] = 0.0

    dets = [d for _, d in all_dets if d["score"] > 0]

    # Cross-stride center suppression
    if len(dets) > 1:
        dets.sort(key=lambda d: d["bbox"][2] * d["bbox"][3])
        for i in range(len(dets)):
            if dets[i]["score"] <= 0:
                continue
            for j in range(i + 1, len(dets)):
                if dets[j]["score"] <= 0:
                    continue
                if _is_same_face_by_center(dets[i], dets[j], CENTER_DIST_THRESH_RATIO):
                    dets[j]["score"] = 0.0

    dets = [d for d in dets if d["score"] > 0]

    # Filter oversized faces
    max_w = img_w * MAX_FACE_RATIO
    max_h = img_h * MAX_FACE_RATIO
    dets = [d for d in dets if d["bbox"][2] <= max_w and d["bbox"][3] <= max_h]
    return dets


def _compute_face_alignment(landmarks: list) -> np.ndarray:
    """Eye-based similarity transform → ArcFace canonical (face_alignment.c)."""
    src_left = np.array(landmarks[LM_LEFT_EYE], dtype=np.float32)
    src_right = np.array(landmarks[LM_RIGHT_EYE], dtype=np.float32)

    src_center = (src_left + src_right) / 2.0
    src_vec = src_right - src_left
    src_dist = float(np.linalg.norm(src_vec))

    dst_left = REFERENCE_LANDMARKS[LM_LEFT_EYE]
    dst_right = REFERENCE_LANDMARKS[LM_RIGHT_EYE]
    dst_center = (dst_left + dst_right) / 2.0
    dst_vec = dst_right - dst_left
    dst_dist = float(np.linalg.norm(dst_vec))

    scale = dst_dist / (src_dist + 1e-6)
    src_angle = np.arctan2(src_vec[1], src_vec[0])
    dst_angle = np.arctan2(dst_vec[1], dst_vec[0])
    angle = dst_angle - src_angle

    cos_a = np.cos(angle) * scale
    sin_a = np.sin(angle) * scale

    M = np.array(
        [
            [cos_a, -sin_a, dst_center[0] - cos_a * src_center[0] + sin_a * src_center[1]],
            [sin_a, cos_a, dst_center[1] - sin_a * src_center[0] - cos_a * src_center[1]],
        ],
        dtype=np.float32,
    )
    return M


def _invert_affine(M: np.ndarray) -> np.ndarray:
    a, b, c = M[0, 0], M[0, 1], M[0, 2]
    d, e, f = M[1, 0], M[1, 1], M[1, 2]
    det = a * e - b * d
    inv_det = 1.0 / (det + 1e-8)
    inv = np.zeros((2, 3), dtype=np.float32)
    inv[0, 0] = e * inv_det
    inv[0, 1] = -b * inv_det
    inv[0, 2] = (b * f - e * c) * inv_det
    inv[1, 0] = -d * inv_det
    inv[1, 1] = a * inv_det
    inv[1, 2] = (d * c - a * f) * inv_det
    return inv


def _apply_face_alignment(bgr_planar: np.ndarray, M: np.ndarray) -> np.ndarray:
    """Backward bilinear warp BGR planar → 112x112 RGB interleaved uint8."""
    src_h, src_w = bgr_planar.shape[1], bgr_planar.shape[2]
    b_plane, g_plane, r_plane = bgr_planar[0], bgr_planar[1], bgr_planar[2]

    inv_M = _invert_affine(M)
    dst = np.zeros((EMB_INPUT_H, EMB_INPUT_W, 3), dtype=np.uint8)

    dy_grid, dx_grid = np.mgrid[0:EMB_INPUT_H, 0:EMB_INPUT_W]
    sx = inv_M[0, 0] * dx_grid + inv_M[0, 1] * dy_grid + inv_M[0, 2]
    sy = inv_M[1, 0] * dx_grid + inv_M[1, 1] * dy_grid + inv_M[1, 2]

    ix = np.floor(sx).astype(np.int32)
    iy = np.floor(sy).astype(np.int32)
    fx = sx - ix
    fy = sy - iy

    valid = (ix >= 0) & (ix < src_w - 1) & (iy >= 0) & (iy < src_h - 1)

    w00 = (1.0 - fx) * (1.0 - fy)
    w01 = fx * (1.0 - fy)
    w10 = (1.0 - fx) * fy
    w11 = fx * fy

    idx00 = iy * src_w + ix
    idx01 = idx00 + 1
    idx10 = idx00 + src_w
    idx11 = idx10 + 1

    v = valid
    for channel, plane in enumerate((r_plane, g_plane, b_plane)):
        dst[dy_grid[v], dx_grid[v], channel] = np.clip(
            w00[v] * plane.flat[idx00[v]]
            + w01[v] * plane.flat[idx01[v]]
            + w10[v] * plane.flat[idx10[v]]
            + w11[v] * plane.flat[idx11[v]]
            + 0.5,
            0,
            255,
        ).astype(np.uint8)

    return dst


def _estimate_face_quality(landmarks: list) -> float:
    left_eye = np.array(landmarks[LM_LEFT_EYE])
    right_eye = np.array(landmarks[LM_RIGHT_EYE])
    nose = np.array(landmarks[LM_NOSE])
    left_mouth = np.array(landmarks[LM_LEFT_MOUTH])
    right_mouth = np.array(landmarks[LM_RIGHT_MOUTH])

    eye_dy = abs(left_eye[1] - right_eye[1])
    eye_dx = abs(left_eye[0] - right_eye[0])
    roll_ratio = eye_dy / (eye_dx + 1e-6)
    quality = max(0.0, 1.0 - roll_ratio * 3.0)

    eye_center_x = (left_eye[0] + right_eye[0]) / 2.0
    nose_offset = abs(nose[0] - eye_center_x)
    nose_range = eye_dx / 2.0
    quality *= max(0.0, 1.0 - nose_offset / (nose_range + 1e-6))

    mouth_center_x = (left_mouth[0] + right_mouth[0]) / 2.0
    mouth_offset = abs(mouth_center_x - eye_center_x)
    quality *= max(0.0, 1.0 - mouth_offset / (nose_range + 1e-6))
    return float(quality)


def _estimate_face_pose(landmarks: list) -> dict:
    left_eye = np.array(landmarks[LM_LEFT_EYE])
    right_eye = np.array(landmarks[LM_RIGHT_EYE])
    nose = np.array(landmarks[LM_NOSE])

    eye_dx = right_eye[0] - left_eye[0]
    eye_dy = right_eye[1] - left_eye[1]
    roll = float(np.arctan2(eye_dy, eye_dx) * 180.0 / np.pi)

    eye_center_x = (left_eye[0] + right_eye[0]) / 2.0
    eye_center_y = (left_eye[1] + right_eye[1]) / 2.0
    eye_width = float(np.hypot(eye_dx, eye_dy))

    nose_offset = nose[0] - eye_center_x
    yaw = float(nose_offset / (eye_width / 2.0 + 1e-6) * 45.0)
    nose_eye_dist = nose[1] - eye_center_y
    expected_dist = eye_width * 0.6
    pitch = float((nose_eye_dist / (expected_dist + 1e-6) - 1.0) * 30.0)
    return {"yaw": yaw, "pitch": pitch, "roll": roll}


# ---------------------------------------------------------------------------
# Simulator singleton — lazy load, thread-safe
# ---------------------------------------------------------------------------

class WE2Simulator:
    """Single-process, lazy, thread-safe WE2 face pipeline.

    First ``infer()`` call loads the two TFLite models with the BUILTIN_REF
    op resolver (this is the bit-exact contract). Subsequent calls reuse the
    cached interpreters. Mutation of the SCRFD / MFN interpreters is serialized
    by ``_invoke_lock`` since tflite Interpreter objects are not reentrant.
    """

    def __init__(
        self,
        scrfd_path: Optional[Path] = None,
        mfn_path: Optional[Path] = None,
    ):
        self.scrfd_path = Path(scrfd_path) if scrfd_path else SCRFD_MODEL_PATH
        self.mfn_path = Path(mfn_path) if mfn_path else MFN_MODEL_PATH
        self.model_tag = MODEL_TAG

        self._init_lock = threading.Lock()
        self._invoke_lock = threading.Lock()
        self._loaded = False

        # Populated on first load
        self._fd_interp = None
        self._fd_input_idx = None
        self._fd_input_dtype = None
        self._fd_input_zp: int = -128

        self._fd_score_tensors: List[Any] = []
        self._fd_bbox_tensors: List[Any] = []
        self._fd_kps_tensors: List[Any] = []
        self._fd_score_scales: List[float] = []
        self._fd_score_zps: List[int] = []
        self._fd_bbox_scales: List[float] = []
        self._fd_bbox_zps: List[int] = []
        self._fd_kps_scales: List[float] = []
        self._fd_kps_zps: List[int] = []

        self._emb_interp = None
        self._emb_input_idx = None
        self._emb_input_dtype = None
        self._emb_in_scale: float = 1.0
        self._emb_in_zp: int = 0
        self._emb_output_idx = None
        self._emb_out_scale: float = 1.0
        self._emb_out_zp: int = 0

    # -- model loading -------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._init_lock:
            if self._loaded:
                return
            self._load_models()
            self._loaded = True

    def _load_models(self) -> None:
        if not self.scrfd_path.exists():
            raise FileNotFoundError(f"SCRFD model missing: {self.scrfd_path}")
        if not self.mfn_path.exists():
            raise FileNotFoundError(f"MFN model missing: {self.mfn_path}")

        # CRITICAL: BUILTIN_REF is the bit-exact contract vs TF 2.19.
        from ai_edge_litert.interpreter import Interpreter, OpResolverType

        # ---- SCRFD ----
        fd = Interpreter(
            model_path=str(self.scrfd_path),
            experimental_op_resolver_type=OpResolverType.BUILTIN_REF,
        )
        fd.allocate_tensors()
        self._fd_interp = fd

        in_det = fd.get_input_details()[0]
        self._fd_input_idx = in_det["index"]
        self._fd_input_dtype = in_det["dtype"]
        in_q = in_det.get("quantization_parameters", {})
        zp_arr = in_q.get("zero_points", np.array([-128]))
        self._fd_input_zp = int(np.asarray(zp_arr).flat[0])

        # Map outputs to per-stride score/bbox/kps tensors
        score_tensors: List[Optional[int]] = []
        bbox_tensors: List[Optional[int]] = []
        kps_tensors: List[Optional[int]] = []
        score_scales: List[float] = []
        score_zps: List[int] = []
        bbox_scales: List[float] = []
        bbox_zps: List[int] = []
        kps_scales: List[float] = []
        kps_zps: List[int] = []

        for od in fd.get_output_details():
            shape = od["shape"]
            qp = od.get("quantization_parameters", {})
            scale = float(np.asarray(qp.get("scales", [1.0])).flat[0])
            zp = int(np.asarray(qp.get("zero_points", [0])).flat[0])

            # Resolve stride index from the spatial / N dimension
            if len(shape) == 4:
                h, w = shape[1], shape[2]
                if h == 20 and w == 20:
                    s_idx = 0
                elif h == 10 and w == 10:
                    s_idx = 1
                elif h == 5 and w == 5:
                    s_idx = 2
                else:
                    continue
            elif len(shape) == 2:
                n_elements = shape[0]
                if n_elements == 800:
                    s_idx = 0
                elif n_elements == 200:
                    s_idx = 1
                elif n_elements == 50:
                    s_idx = 2
                else:
                    continue
            else:
                continue

            ch = shape[-1]
            # score: channel==1 (or [...,2] in 4-d packed layout)
            # bbox:  channel==4 (or 8 in packed)
            # kps:   channel==10 (or 20 in packed)
            def _pad(lst, idx, fill):
                while len(lst) <= idx:
                    lst.append(fill)

            if ch == 1 or (len(shape) == 4 and shape[3] == 2):
                _pad(score_tensors, s_idx, None)
                _pad(score_scales, s_idx, 1.0)
                _pad(score_zps, s_idx, 0)
                score_tensors[s_idx] = od["index"]
                score_scales[s_idx] = scale
                score_zps[s_idx] = zp
            elif ch == 4 or (len(shape) == 4 and shape[3] == 8):
                _pad(bbox_tensors, s_idx, None)
                _pad(bbox_scales, s_idx, 1.0)
                _pad(bbox_zps, s_idx, 0)
                bbox_tensors[s_idx] = od["index"]
                bbox_scales[s_idx] = scale
                bbox_zps[s_idx] = zp
            elif ch == 10 or (len(shape) == 4 and shape[3] == 20):
                _pad(kps_tensors, s_idx, None)
                _pad(kps_scales, s_idx, 1.0)
                _pad(kps_zps, s_idx, 0)
                kps_tensors[s_idx] = od["index"]
                kps_scales[s_idx] = scale
                kps_zps[s_idx] = zp

        if (
            len(score_tensors) < SCRFD_NUM_STRIDES
            or len(bbox_tensors) < SCRFD_NUM_STRIDES
            or len(kps_tensors) < SCRFD_NUM_STRIDES
            or None in score_tensors
            or None in bbox_tensors
            or None in kps_tensors
        ):
            raise RuntimeError(
                "SCRFD output mapping incomplete — model topology not recognized"
            )

        self._fd_score_tensors = score_tensors
        self._fd_bbox_tensors = bbox_tensors
        self._fd_kps_tensors = kps_tensors
        self._fd_score_scales = score_scales
        self._fd_score_zps = score_zps
        self._fd_bbox_scales = bbox_scales
        self._fd_bbox_zps = bbox_zps
        self._fd_kps_scales = kps_scales
        self._fd_kps_zps = kps_zps

        # ---- MFN ----
        emb = Interpreter(
            model_path=str(self.mfn_path),
            experimental_op_resolver_type=OpResolverType.BUILTIN_REF,
        )
        emb.allocate_tensors()
        self._emb_interp = emb

        emb_in = emb.get_input_details()[0]
        self._emb_input_idx = emb_in["index"]
        self._emb_input_dtype = emb_in["dtype"]
        emb_in_q = emb_in.get("quantization_parameters", {})
        self._emb_in_scale = float(
            np.asarray(emb_in_q.get("scales", [1.0])).flat[0]
        )
        self._emb_in_zp = int(np.asarray(emb_in_q.get("zero_points", [0])).flat[0])

        emb_out = emb.get_output_details()[0]
        self._emb_output_idx = emb_out["index"]
        emb_out_q = emb_out.get("quantization_parameters", {})
        self._emb_out_scale = float(
            np.asarray(emb_out_q.get("scales", [1.0])).flat[0]
        )
        self._emb_out_zp = int(np.asarray(emb_out_q.get("zero_points", [0])).flat[0])

    # -- inference -----------------------------------------------------------

    def infer(self, image: Image.Image) -> dict:
        """Run the full pipeline; return face_rec_api-shaped payload.

        Returns ``{"faces": [...], "face_count": N, "model_tag": ..., ...}``.
        Each face entry includes ``embedding_bytes`` (raw int8) and metadata.
        Caller (router) is responsible for base64-encoding embedding_bytes.
        """
        self._ensure_loaded()
        img_w, img_h = image.size  # PIL: (width, height)

        bgr_planar = _image_to_bgr_planar(image)
        fd_input_rgb = _resize_bgr_planar_to_rgb_interleaved(
            bgr_planar, FD_INPUT_W, FD_INPUT_H
        )

        with self._invoke_lock:
            # SCRFD
            if self._fd_input_dtype == np.int8:
                fd_input = _quantize_uint8_to_int8(fd_input_rgb, self._fd_input_zp)
            else:
                fd_input = fd_input_rgb
            fd_input = fd_input[np.newaxis, ...]

            self._fd_interp.set_tensor(self._fd_input_idx, fd_input)
            self._fd_interp.invoke()

            score_data = [self._fd_interp.get_tensor(t) for t in self._fd_score_tensors]
            bbox_data = [self._fd_interp.get_tensor(t) for t in self._fd_bbox_tensors]
            kps_data = [self._fd_interp.get_tensor(t) for t in self._fd_kps_tensors]

        dets = _scrfd_decode_and_nms(
            score_data,
            bbox_data,
            kps_data,
            self._fd_score_scales,
            self._fd_score_zps,
            self._fd_bbox_scales,
            self._fd_bbox_zps,
            self._fd_kps_scales,
            self._fd_kps_zps,
            FD_INPUT_W,
            FD_INPUT_H,
            img_w,
            img_h,
            FACE_CONF_THRESHOLD,
            FACE_NMS_THRESHOLD,
        )
        # Sort dets by score desc so picker has stable ordering
        dets.sort(key=lambda d: d["score"], reverse=True)

        faces_out: List[dict] = []
        for det in dets:
            bw, bh = det["bbox"][2], det["bbox"][3]
            if bw < MIN_FACE_SIZE or bh < MIN_FACE_SIZE:
                continue

            M = _compute_face_alignment(det["landmarks"])
            aligned = _apply_face_alignment(bgr_planar, M)

            # Embedding model input quantization
            if self._emb_input_dtype == np.int8:
                emb_input = _quantize_embedding_input_rgb(
                    aligned, self._emb_in_scale, self._emb_in_zp
                )
            elif self._emb_input_dtype == np.float32:
                emb_input = (aligned.astype(np.float32) / 127.5) - 1.0
            else:
                emb_input = aligned
            emb_input = emb_input[np.newaxis, ...]

            with self._invoke_lock:
                self._emb_interp.set_tensor(self._emb_input_idx, emb_input)
                self._emb_interp.invoke()
                emb_output = self._emb_interp.get_tensor(self._emb_output_idx)

            # Raw int8 bytes — no dequantize, no L2 normalize. Matcher does
            # cosine over int8 vectors.
            emb_int8 = np.asarray(emb_output, dtype=np.int8).flatten()
            if emb_int8.size > EMB_OUTPUT_DIM:
                emb_int8 = emb_int8[:EMB_OUTPUT_DIM]
            embedding_bytes = emb_int8.tobytes()

            faces_out.append(
                {
                    "bbox": [float(v) for v in det["bbox"]],
                    "landmarks": [[float(x), float(y)] for x, y in det["landmarks"]],
                    "det_score": float(det["score"]),
                    "embedding_bytes": embedding_bytes,
                    "aligned_face": aligned,  # numpy array, router decides if returned
                    "quality": _estimate_face_quality(det["landmarks"]),
                    "pose": _estimate_face_pose(det["landmarks"]),
                }
            )

        return {
            "faces": faces_out,
            "face_count": len(faces_out),
            "model_tag": self.model_tag,
        }


# ---------------------------------------------------------------------------
# Process-wide singleton accessor
# ---------------------------------------------------------------------------

_singleton: Optional[WE2Simulator] = None
_singleton_lock = threading.Lock()


def get_simulator() -> WE2Simulator:
    """Return the process-wide WE2Simulator (created on first call)."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = WE2Simulator()
    return _singleton
