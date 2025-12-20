---
description: Comprehensive guide for using the Warehouse Management System core features
title: Warehouse System User Guide
sidebar_position: 7
keywords:
- Warehouse
- Guide
- Excel Import
- Database
- Stock Management
image: http://files.seeedstudio.com/wiki/SenseCAP-Watcher-for-Xiaozhi-AI/Watcher_Agent.webp
slug: /warehouse_system_guide
last_update:
  date: 12/09/2025
  author: Seeed Studio
---

# Warehouse System User Guide

## Overview

This guide complements the [MCP Integration Guide](/mcp_external_system_integration) by focusing on the core functionalities of the Warehouse Management System itself. It is designed to help operators and administrators quick start with the system.

## 1. Database Management

The system uses SQLite for data storage, which is lightweight and requires no complex installation.

### 1.1 Initialization

The database is **automatically initialized** when you start the backend service if it doesn't already exist.

1.  **Start the Backend**:
    ```bash
    uv run python run_backend.py
    ```
2.  **Auto-Creation**:
    - The system checks for `warehouse.db` in the root directory.
    - If missing, it creates the file and populates it with **initial demo data** (Watcher Xiaozhi products).
    - You will see the log: `Database initialized with sample data`.

### 1.2 Resetting the Database

If you want to clear all data and start fresh:

1.  **Stop the Backend**: Press `Ctrl+C` in your terminal.
2.  **Delete the Database File**:
    ```bash
    rm warehouse.db
    ```
3.  **Restart the Backend**:
    ```bash
    uv run python run_backend.py
    ```
    A new database with default demo data will be created.

## 2. Stock Operations (Inbound/Outbound)

Daily stock movements can be recorded manually through the web interface.

### 2.1 Accessing the Interface

Navigate to `http://localhost:2124` (or your server address) in your browser.

### 2.2 Creating a Record

1.  Click on the **"Records" (进出库记录)** tab in the sidebar.
2.  Click the **"Add Record" (新增记录)** button in the top right.
3.  Fill in the form:
    - **Product**: Select the product from the dropdown list.
    - **Type**: Choose "Inbound" (入库) or "Outbound" (出库).
    - **Quantity**: Enter the number of units.
    - **Operator**: Enter your name.
    - **Reason**: E.g., "Purchase", "Sales", "Return".
4.  Click **"Submit"**.

:::tip
The system automatically checks for stock availability during outbound operations. You cannot stock out more items than currently available.
:::

## 3. Excel Management (Smart Import/Export)

To handle large-scale data updates, the system provides a robust Excel import/export feature.

### 3.1 Exporting Data

You can export current inventory data to Excel for reporting or backup.

1.  Go to the **"Inventory List" (库存列表)** tab.
2.  Filters (optional): Select a category or search for a product to export only specific data.
3.  Click **"Export Inventory" (导出库存)**.
4.  A `.xlsx` file will be downloaded automatically.

### 3.2 Smart Import (Update via Excel)

The system supports a intelligent import mechanism that can **update existing stock** based on the Excel file. It automatically calculates the difference between the Excel quantity and the system quantity.

#### How it Works

When you upload an Excel file, the system matches products by **SKU**:
- **Match Found**: It compares the `Import Quantity` with the `System Quantity`.
    - If `Import > System`: Creates an **Inbound** record for the difference.
    - If `Import < System`: Creates an **Outbound** record for the difference.
    - Updates other fields like Safe Stock, Location, Category, etc.
- **No Match**: It identifies this as a **New Product** and creates it.

#### Step-by-Step Import

1.  Go to the **"Inventory List" (库存列表)** tab.
2.  Click **"Import Inventory" (导入库存)**.
3.  **Upload File**: Select your `.xlsx` file.
    > **Note**: You can first "Export" a template to ensure the correct format.
4.  **Preview Changes**:
    The system will show a preview of all changes:
    - **Green Rows**: Inbound operations (Stock increasing).
    - **Orange Rows**: Outbound operations (Stock decreasing).
    - **Blue Rows**: New products to be created.
5.  **Confirm**:
    - Enter "Operator" and "Import Reason".
    - (Optional) Check "Disable Missing SKUs" to disable products not present in the Excel file.
    - Click **"Confirm Import"**.

:::important
This "Smart Import" feature ensures your inventory records (in/out logs) are always complete, even when doing bulk updates via Excel. It doesn't just overwrite the number; it generates the audit trail for you.
:::

## 4. Technical Support

<div class="button_tech_support_container">
<a href="https://discord.com/invite/QqMgVwHT3X" class="button_tech_support_sensecap"></a>
<a href="https://support.sensecapmx.com/portal/en/home" class="button_tech_support_sensecap3"></a>
</div>

<div class="button_tech_support_container">
<a href="mailto:support@sensecapmx.com" class="button_tech_support_sensecap2"></a>
<a href="https://github.com/Seeed-Studio/wiki-documents/discussions/69" class="button_discussion"></a>
</div>
