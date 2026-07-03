"""WE2 face embedding simulator.

A host-side, bit-exact mirror of the WE2 NPU pipeline used by the on-device
face_rec_api. Enables face enrollment from a phone/desktop photo while still
producing embeddings identical to what the WE2 device computes in production.

See ``simulator.py`` for the pipeline and ``WE2Simulator`` singleton.
"""

from .simulator import WE2Simulator, MODEL_TAG, get_simulator

__all__ = ["WE2Simulator", "MODEL_TAG", "get_simulator"]
