"""
Export tests: Excel export for materials and inventory records.
"""
import pytest
from io import BytesIO


class TestMaterialsExport:
    """Materials Excel export tests."""

    def test_export_materials_excel(self, admin_client, sample_material):
        """Export materials should return a valid Excel file."""
        resp = admin_client.get("/api/materials/export-excel")
        assert resp.status_code == 200
        content_type = resp.headers.get('content-type', '')
        assert 'spreadsheet' in content_type or 'octet-stream' in content_type

    def test_export_materials_has_content(self, admin_client, sample_material):
        """Exported Excel should have actual content."""
        resp = admin_client.get("/api/materials/export-excel")
        assert resp.status_code == 200
        assert len(resp.content) > 0

        # Verify it's a valid xlsx
        from openpyxl import load_workbook
        wb = load_workbook(filename=BytesIO(resp.content))
        ws = wb.active
        # Should have at least header row + data row
        assert ws.max_row >= 2


class TestRecordsExport:
    """Inventory records Excel export tests."""

    def test_export_records_excel(self, admin_client):
        """Export records should return a valid Excel file."""
        resp = admin_client.get("/api/inventory/export-excel")
        assert resp.status_code == 200
        content_type = resp.headers.get('content-type', '')
        assert 'spreadsheet' in content_type or 'octet-stream' in content_type

    def test_export_records_has_content(self, admin_client):
        """Exported records Excel should have actual data."""
        resp = admin_client.get("/api/inventory/export-excel")
        assert resp.status_code == 200
        assert len(resp.content) > 0

        from openpyxl import load_workbook
        wb = load_workbook(filename=BytesIO(resp.content))
        ws = wb.active
        assert ws.max_row >= 1  # At least header

    def test_export_records_operator_face_name_composed(self, admin_client,
                                                        sample_material):
        """操作人 cell shows "operator (face_name)" when face name was snapshot."""
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sample_material['name'],
            "quantity": 7,
            "reason_category": "purchase",
            "warehouse_id": sample_material['warehouse_id'],
            "operator_face_name": "张三",
        })
        assert resp.status_code == 200 and resp.json()['success'] is True

        resp = admin_client.get("/api/inventory/export-excel",
                                params={"product_name": sample_material['name']})
        assert resp.status_code == 200

        from openpyxl import load_workbook
        wb = load_workbook(filename=BytesIO(resp.content))
        ws = wb.active
        headers = [c.value for c in ws[1]]
        op_col = headers.index('操作人') + 1
        operator_cells = [ws.cell(row=r, column=op_col).value
                          for r in range(2, ws.max_row + 1)]
        assert any(v and v.endswith(' (张三)') for v in operator_cells), \
            operator_cells
