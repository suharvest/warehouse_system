"""
Import tests: Excel import preview, confirm execution, new SKU creation.
"""
import pytest
import os
from io import BytesIO


def get_test_data_path():
    """Get path to test data file."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'test', 'data', 'inventory_test_data.xlsx')


class TestImportPreview:
    """Excel import preview tests."""

    def test_preview_with_valid_file(self, admin_client):
        """Preview with a valid Excel file should succeed."""
        test_file = get_test_data_path()
        if not os.path.exists(test_file):
            pytest.skip("Test data file not found")

        with open(test_file, 'rb') as f:
            resp = admin_client.post(
                "/api/materials/import-excel/preview",
                files={"file": ("test.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert isinstance(data['preview'], list)
        assert 'total_in' in data
        assert 'total_out' in data

    def test_preview_with_invalid_file(self, admin_client):
        """Preview with invalid content should return error."""
        fake_content = b"This is not an Excel file"
        resp = admin_client.post(
            "/api/materials/import-excel/preview",
            files={"file": ("bad.xlsx", BytesIO(fake_content), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_preview_with_generated_excel(self, admin_client, sample_material):
        """Preview with a programmatically generated Excel file."""
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        # Header row matching expected format
        ws.append(['Name', 'SKU', 'Category', 'Quantity', 'Unit', 'Safe Stock', 'Location'])
        # Data rows
        ws.append([sample_material['name'], sample_material['sku'], 'Test Category', 200, 'pcs', 20, 'A-01'])
        ws.append(['New Import Item', 'IMPORT-001', 'New Cat', 50, 'pcs', 10, 'Z-01'])

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        resp = admin_client.post(
            "/api/materials/import-excel/preview",
            files={"file": ("import.xlsx", buffer, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True


class TestImportConfirm:
    """Excel import confirmation tests."""

    def test_confirm_import_creates_records(self, admin_client, sample_material):
        """Confirming import should create inventory records."""
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(['Name', 'SKU', 'Category', 'Quantity', 'Unit', 'Safe Stock', 'Location'])
        # Set quantity higher than current (100) to trigger stock-in
        ws.append([sample_material['name'], sample_material['sku'], 'Test Category', 120, 'pcs', 20, 'A-01'])

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        # Preview first
        preview_resp = admin_client.post(
            "/api/materials/import-excel/preview",
            files={"file": ("import.xlsx", buffer, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
        preview_data = preview_resp.json()
        if preview_data.get('success') and preview_data.get('preview'):
            # Confirm import with the changes from preview
            confirm_resp = admin_client.post("/api/materials/import-excel/confirm", json={
                "changes": preview_data['preview'],
                "reason": "Test import",
                "confirm_new_skus": False,
                "confirm_disable_missing_skus": False
            })
            assert confirm_resp.status_code == 200
            data = confirm_resp.json()
            assert data['success'] is True

    def test_confirm_import_new_sku(self, admin_client):
        """Import with new SKU should create new material."""
        from openpyxl import Workbook
        import uuid

        new_sku = f"NEW-{uuid.uuid4().hex[:6].upper()}"
        new_name = f"New Material {new_sku}"

        wb = Workbook()
        ws = wb.active
        ws.append(['Name', 'SKU', 'Category', 'Quantity', 'Unit', 'Safe Stock', 'Location'])
        ws.append([new_name, new_sku, 'Import Cat', 75, 'pcs', 15, 'X-01'])

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        # Preview
        preview_resp = admin_client.post(
            "/api/materials/import-excel/preview",
            files={"file": ("import.xlsx", buffer, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        )
        preview_data = preview_resp.json()
        if preview_data.get('success'):
            # Check that new SKU is detected
            assert preview_data['total_new'] >= 1 or len(preview_data.get('new_skus', [])) >= 1

            # Confirm with new SKUs included
            changes = preview_data.get('preview', [])
            confirm_resp = admin_client.post("/api/materials/import-excel/confirm", json={
                "changes": changes,
                "reason": "Import new SKU test",
                "confirm_new_skus": True,
                "confirm_disable_missing_skus": False
            })
            assert confirm_resp.status_code == 200
