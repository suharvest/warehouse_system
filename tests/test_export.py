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
