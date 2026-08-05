try:
    from providers.base import BaseProvider
except ImportError:
    from ..base import BaseProvider


class UsersDemoProvider(BaseProvider):
    PROVIDER_NAME = "demo_wms"
    def resolve_name(self, text, entity_type="all"): return {"best_match": None, "confident": False, "candidates": []}
    def query_stock(self, product_name, show_batches=False): return {"success": False, "error": "not_found", "message": "demo"}
    def stock_in(self, product_name, quantity, reason_category, reason_note, operator, fuzzy, location=None, contact_id=None, variant=None, allow_new_variant=False, actual_operator=None): return {"success": False, "error": "not_implemented", "message": "demo"}
    def stock_out(self, product_name, quantity, reason_category, reason_note, operator, fuzzy, variant=None, location=None, batch_no=None, location_fuzzy=False, allow_partial_fallback=False, actual_operator=None): return {"success": False, "error": "not_implemented", "message": "demo"}
    def search(self, query, entity_type, category, status, contact_type, fuzzy, include_batches=False, max_results=0): return {"success": True, "count": 0, "total": 0, "items": [], "message": "demo"}
    def get_today_statistics(self): return {"success": False, "error": "not_implemented", "message": "demo"}
    def list_users(self, tenant_id=None):
        return {"success": True, "items": [
            {"id": "u1001", "name": "zhangsan", "display_name": "张三"},
            {"id": "u1002", "name": "lisi", "display_name": "李四"},
            {"id": "u1003", "name": "admin", "display_name": "王五"},
        ], "message": "ok"}
