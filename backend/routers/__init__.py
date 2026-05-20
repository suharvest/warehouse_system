# Routers package (Phase 1+ app.py split, task #5).
# Per-domain APIRouter modules live here; do not import them at package
# init time to avoid circular imports — app.py imports each router by
# module path and calls ``app.include_router(...)`` explicitly.
