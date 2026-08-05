# MCP Integration Guide

English | [中文](README.md)

This directory is the warehouse system's **MCP integration layer**. It does two things:

1. Exposes warehouse capabilities (stock query / inbound / outbound / search / batches / statistics) as MCP tools, callable from voice devices (SenseCAP Watcher + Xiaozhi) or any MCP host (Claude Desktop, etc.);
2. Through the **Provider abstraction**, lets you swap the backend behind those tools for any third-party WMS/ERP — with zero changes to the tool layer.

So there are two integration paths. Pick yours:

| Your situation | Path | What you write | Main doc |
|---|---|---|---|
| **You already have your own system** (WMS / CRM / ERP / in-house backend) and want voice/AI to drive it | **Path A: wrap your system as MCP** | A Provider subclass (or your own FastMCP server) | §2 below + [MCP_External_System_Integration.md](../docs/MCP_External_System_Integration.md) |
| **You use someone else's system** (third-party WMS/ERP/SaaS) and want it bridged into our warehouse system + devices | **Path B: write a bridge Provider and upload it to us** | One Provider `.py` file, uploaded via the Web UI | §3 below + [WMS_Provider_Development.md](../docs/WMS_Provider_Development.md) |

Both paths share the same Provider interface. The only difference is **who runs it**: in Path A you deploy the MCP process yourself; in Path B you hand us the file and our backend validates, hosts, and launches it.

---

## 1. Component map

```
Voice device / MCP host
        │  (WebSocket wss://  or  stdio)
        ▼
   mcp_pipe.py            ← WS ↔ stdio pipe: reconnect w/ backoff, protocol logging
        │  stdio (JSON-RPC)
        ▼
   warehouse_mcp.py       ← MCP tool layer (8 @mcp.tool), face-permission guard, anti-hallucination wrapper
        │  Provider interface (6 required + 2 optional methods)
        ▼
   providers/
     ├── base.py          ← BaseProvider: HTTP helpers + auth + abstract methods
     ├── default.py       ← Talks to this repo's built-in FastAPI backend
     ├── validator.py     ← AST security scan + structural validation for uploads
     ├── test_runner.py   ← Level 1 / Level 2 connectivity tests
     └── custom/          ← User-uploaded third-party Providers (auto-discovered)
```

| File | Role |
|---|---|
| `warehouse_mcp.py` | Tool definitions and response shaping — **never calls a backend directly** |
| `mcp_pipe.py` | Bridges a stdio MCP server to a `wss://` endpoint; exponential-backoff reconnect + JSON-RPC event log |
| `start_mcp.sh` / `start_mcp.ps1` | Local launch (checks uv, probes backend, starts the pipe) |
| `config.yml.example` | Config template: `provider` / `api_base_url` / `auth` / `timeout` |
| `providers/` | Provider registry; auto-scans this directory and `custom/` |

**Current tool set** (source of truth: `@mcp.tool()` in `warehouse_mcp.py`):

| Tool | Purpose |
|---|---|
| `resolve_name(text, entity_type)` | Fuzzy text → exact entity name (disambiguate ASR output) |
| `query_stock(product_name)` | Query inventory |
| `query_batch(batch_no)` | Query batch |
| `stock_in(product_name, quantity, ...)` | Record inbound |
| `stock_out(product_name, quantity, ...)` | Record outbound |
| `search(query, entity_type, ...)` | Unified search (materials / contacts / operators) |
| `move_batch_location(batch_no, new_location, ...)` | Relocate a batch |
| `get_today_statistics()` | Daily summary |

Field-level response examples: [MCP_README_EN.md](MCP_README_EN.md).

---

## 2. Path A: wrap your own system as MCP and connect it to our devices

For "I have a system, I want voice control over it." Two options — **prefer A1**.

### A1 (recommended): write only a Provider, reuse the whole bridge

You reuse `mcp_pipe.py` + `warehouse_mcp.py` + the tool descriptions (already tuned for voice: disambiguation, candidate read-back, quantity confirmation, anti-hallucination) and swap only the data source.

**1) Create the Provider**

Add `my_wms.py` under `mcp/providers/`:

```python
from .base import BaseProvider


class MyWmsProvider(BaseProvider):
    PROVIDER_NAME = "my_wms"          # matches the `provider` field in config.yml

    def __init__(self, config: dict):
        super().__init__(config)
        self.warehouse_id = config.get("warehouse_id", "")   # custom config field

    def resolve_name(self, text, entity_type="all"): ...
    def query_stock(self, product_name, show_batches=False): ...
    def stock_in(self, product_name, quantity, reason_category, reason_note,
                 operator, fuzzy, location=None, contact_id=None,
                 variant=None, allow_new_variant=False, actual_operator=None): ...
    def stock_out(self, product_name, quantity, reason_category, reason_note,
                  operator, fuzzy, variant=None, location=None, batch_no=None,
                  location_fuzzy=False, allow_partial_fallback=False,
                  actual_operator=None): ...
    def search(self, query, entity_type, category, status, contact_type, fuzzy,
               include_batches=False, max_results=0): ...
    def get_today_statistics(self): ...

    # Optional (tools return not_implemented if omitted)
    def query_batch(self, batch_no): ...
    def move_batch_location(self, batch_no, new_location, quantity=None,
                            from_location=None, product_name=None,
                            operator="MCP系统"): ...
```

> ⚠️ **`providers/base.py` is the source of truth, and parameter order is fixed** — both the tool layer and the connectivity tests pass arguments positionally. Per-argument semantics, return-format contracts, and a copy-pasteable full example are in [WMS_Provider_Development.md](../docs/WMS_Provider_Development.md).

The base class provides `http_get()` / `http_post()` with automatic auth headers and exceptions collapsed into `{"error": ...}`. Auth type (`api_key` / `bearer` / `basic` / `custom`) is declared in the `auth` block of `config.yml` — you don't hand-roll headers.

`actual_operator` is the name the face recognition actually matched (see §2.5), tracked separately from the LLM-supplied `operator`; it's always `None` when face recognition is off.

**2) Configure**

```bash
cd mcp && cp config.yml.example config.yml
```

```yaml
provider: "my_wms"
api_base_url: "https://your-wms.example.com/api/v1"
auth:
  type: bearer
  token: "your-token"
timeout: 15
warehouse_id: "WH-001"     # any custom field, passed through to the Provider
```

Environment variables take precedence: `WAREHOUSE_API_URL` / `WAREHOUSE_API_KEY` / `WAREHOUSE_PROVIDER`.

**3) Start**

```bash
export MCP_ENDPOINT="wss://<your MCP endpoint>"
./start_mcp.sh
```

The log line `使用 provider: my_wms (MyWmsProvider)` confirms the switch.

Or skip the WS endpoint and attach to an MCP host over stdio:

```json
{
  "mcpServers": {
    "warehouse-system": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "python", "warehouse_mcp.py"],
      "cwd": "/absolute/path/to/warehouse_system/mcp"
    }
  }
}
```

See [docs/CLAUDE_DESKTOP_CONFIG.md](../docs/CLAUDE_DESKTOP_CONFIG.md).

**4) Let our backend host it (optional)**

If you don't want to babysit the process, register the MCP endpoint in the Web UI's MCP connection manager; the backend's `mcp_manager` handles launch, reconnect, logs, and device binding:

| Endpoint | Purpose |
|---|---|
| `POST /api/mcp/connections` | Create a connection (endpoint + API key) |
| `POST /api/mcp/connections/{id}/start` \| `/stop` \| `/restart` | Lifecycle |
| `GET /api/mcp/connections/{id}/logs` | Pull protocol logs for debugging |
| `GET/POST /api/mcp/connections/{id}/devices` | Bind devices to the connection |

### 2.5 The face-recognition gate (required reading for A1)

**The face gate lives in the tool layer, not the Provider layer.** All 8 tools in `warehouse_mcp.py` run `_enforce_face()` **before** calling your Provider (write tools use their own operation; `search` / `query_stock` / `query_batch` / `get_today_statistics` / `resolve_name` use `operation="query"`). Meaning: **A1 inherits the face gate unconditionally — swapping the Provider does not remove it.**

The gate is a single HTTP call:

```
POST {api_base_url}/face/verify-mcp
→ {"status": "pass" | "skipped" | "deny", "failure_reason", "confidence",
   "matched_subject_id", "matched_subject_name"}
```

`pass` / `skipped` proceed, `deny` aborts. The policy is **fail-closed**: better to block than to silently let something through.

#### ⚠️ The trap: the gate and the Provider share one `api_base_url`

`_face_guard()` reads `config['api_base_url']`, and `BaseProvider.__init__` reads that same field into `self.base_url`. So the moment you repoint `api_base_url` at your own WMS, the gate starts looking for `/face/verify-mcp` there — and not finding it means `deny`, which **takes down all 8 tools, including read-only queries**.

Verified with a local probe (measured, not inferred):

| `api_base_url` points at | `_face_guard()` returns | Effect |
|---|---|---|
| Your WMS, no `/face/verify-mcp` (404) | `{"status": "deny", "failure_reason": "http_404"}` | **all 8 tools blocked** |
| An unreachable address | `{"status": "deny", "failure_reason": "transport_error"}` | **all 8 tools blocked** |
| Empty string | `{"status": "skipped", "failure_reason": "no_api_base"}` | proceeds, but the Provider loses its base_url too |
| Our backend | Decided by tenant rules | normal |

#### Three ways out

**Option 1 (recommended): keep `api_base_url` on our backend, give the Provider its own field**

The gate keeps working (face recognition can be on or off, decided by rules in our backend) while your Provider reads its WMS address from a custom config field:

```yaml
provider: "my_wms"
api_base_url: "http://localhost:2124/api"     # reserved for the face gate
auth:
  type: api_key
  key: "wh_xxx"                                # our system's API key
wms_base_url: "https://your-wms.example.com/api/v1"   # your WMS
wms_token: "your-token"
```

```python
class MyWmsProvider(BaseProvider):
    PROVIDER_NAME = "my_wms"

    def __init__(self, config: dict):
        super().__init__(config)
        # Override the base class's base_url so http_get/http_post hit your WMS
        self.base_url = config.get("wms_base_url", "").rstrip("/")
        self._token = config.get("wms_token", "")

    def get_auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}
```

No core code changes, and the two credential sets stay independent.

**Option 2: implement `/face/verify-mcp` on your own backend**

For deployments that don't talk to our backend at all. If you don't want face recognition, return a constant stub:

```python
@app.post("/api/face/verify-mcp")
def verify_mcp():
    return {"status": "skipped", "failure_reason": "feature_disabled",
            "confidence": None, "matched_subject_id": None,
            "matched_subject_name": None}
```

To actually do face recognition, return `pass` with `matched_subject_name` — that name lands in the stock record as `actual_operator`. Request body fields: `operation` (`stock_in` / `stock_out` / `move_batch_location` / `query`), `warehouse_id`, and optional `image_b64` / `embedding_b64` / `embedding_model_tag`.

Note the timeout budget is **18 seconds** (the backend may synchronously pull a frame from a device and run inference). Don't exceed it, or you'll be judged a `transport_error` and fail closed.

**Option 3: leave `api_base_url` empty**

`_face_guard()` returns `no_api_base` → `skipped`, bypassing the gate entirely. But `BaseProvider.base_url` will also be empty, so pair this with the custom field from Option 1.

#### Do you need face-related columns in your database?

**You don't need to create a single face-related table.** Subjects (`face_subjects`), the feature library (`face_enrollments`), audit records (`face_auth_logs`), and the permission rules all live in our DB — and our DB holds the master face library (the copy on the device is pushed down via `push-faces`). Your database needs no changes.

Exactly **one string** crosses the boundary into your system — the name of the person the face gate actually recognized:

```
Face matches "Zhang San" → tool layer sets actual_operator="Zhang San" → passed to your Provider.stock_in(...)
```

That's all of it. So you have three choices:

| Choice | What you do | Notes |
|---|---|---|
| **No audit trail** | Ignore the `actual_operator` argument in your Provider | **Zero changes.** The gate still blocks as configured; your DB just won't record *who* performed it. Our `face_auth_logs` still has the full trail, viewable in our UI |
| **Audit trail** (recommended) | Add one column to your stock-movement table | See below |
| **Map to an existing column** | Write it into your existing "handler / operator" field | Don't merge it with `operator` — see point 3 |

For the audit trail, mirror our `inventory_records.actual_operator` definition:

```sql
-- Snapshot of the name the face gate recognized
ALTER TABLE <your stock movement table>
  ADD COLUMN actual_operator VARCHAR(255) NULL;
```

Three constraints:

1. **Must be nullable.** For non-face operations, or when no rule requires face, the value is always `NULL` — don't make it NOT NULL.
2. **Stores a name string, not a foreign key.** Your DB has no `face_subjects` table, so a `subject_id` would be meaningless; we pass a name snapshot.
3. **`operator` and `actual_operator` are two independent fields — don't merge them.** `operator` is LLM-supplied (defaults to `"MCP系统"`, forgeable via prompt injection); `actual_operator` comes from face verification (trustworthy). Merging them contaminates a trusted field with an untrusted one — that distinction is the entire value of the face audit trail.

#### When the gate actually blocks, if you use our backend

`/face/verify-mcp` requires the API key to hold `FACE:WRITE`. The backend returns `skipped` (proceed) in these cases:

- Deployment switch off (`FACE_ENABLED=false`; the cloud edition has no face support) → `feature_disabled`
- Tenant hasn't enabled face recognition or has no config → `feature_disabled`
- No rule requires face for this operation → `rule_not_required`
- The API key belongs to a global admin with no concrete tenant context → `no_tenant_context`

In other words: **our backend + no face rules configured = the gate is transparent**, and you need to do nothing. It only blocks once you explicitly configure a rule.

### A2: write your own MCP server (non-warehouse domains)

If your domain isn't warehousing (CRM, ticketing, IT ops), the 6 Provider methods won't map. Borrow `mcp_pipe.py` purely as transport and define your own FastMCP tools. This route **does not bring the face gate along** (it lives in `warehouse_mcp.py`, which you aren't using); if you do want face authentication, POST to our `/face/verify-mcp` at the top of your tool and honour the semantics in §2.5.

```python
@mcp.tool()
def check_order_status(order_id: str) -> dict:
    """
    Check the status of a customer order.
    Use this when the user asks about order tracking or delivery status.

    Args:
        order_id: The unique order identifier (e.g., "ORD-2024-001")
    """
    return api_get(f"/orders/{order_id}/status")
```

```bash
export MCP_ENDPOINT="wss://<your MCP endpoint>"
uv run python mcp_pipe.py your_server.py
```

**Hard constraints for voice scenarios** (learned the hard way — don't skip):

- **Never `print()`**: stdio *is* the protocol channel; any stdout write corrupts JSON-RPC framing. Use `logging` (goes to stderr).
- **Keep return payloads small**: an LLM reads them to produce spoken replies. Stay under ~1KB. Cloud frames are ~13KB, and long lists trigger WebSocket close 1009 — that's why `max_results` defaults to 10 in `warehouse_mcp.py`.
- **The docstring is the tool's UI**: the LLM uses it to decide *when* to call and *how* to fill arguments. Describe intent triggers, not just parameter types.
- **Name things readably**: `query_xiaozhi_stock`, not `qry_stk`.
- **Swallow exceptions and return them structurally**: `{"success": False, "error": ..., "message": ...}` so the LLM can explain the failure instead of the connection dying.
- **Restart the MCP process after code changes** — the tool list is reported at handshake time.

---

## 3. Path B: bridge someone else's system into ours

For "I don't have my own warehouse system — I use a third-party WMS/ERP, but I want your devices + frontend + voice."

You deliver **one `.py` file**, uploaded from the Web UI. Our backend handles the security scan, connectivity tests, and activation. The MCP tool layer and frontend stay untouched.

### 3.1 System modes

Two modes exist (`system_settings.system_mode`):

| Mode | Where data lives |
|---|---|
| `self_owned` (default) | This system's own database |
| `external_erp` | Your third-party ERP; this system is only the UI + voice entry point |

Switch with `PUT /api/system/mode {"mode": "external_erp"}`. **Precondition: an active Provider must already exist**, otherwise 400.

### 3.2 Before upload: your file must pass validation

`providers/validator.py` runs a static AST scan; failures are rejected with 400:

| Rule | Detail |
|---|---|
| ≤ 100KB, `.py` extension | Hard limit |
| Forbidden imports | `os` `sys` `subprocess` `shutil` `socket` `ctypes` `code` `codeop` |
| Forbidden calls | `eval` `exec` `compile` `open` `__import__` |
| ⚠️ Forbidden calls match **by function name** | Attribute calls included. So `re.compile(...)` is flagged as `*.compile()` — even though `re` is whitelisted. Call `re.match` / `re.search` directly instead of precompiling |
| Recommended imports only | `requests` `json` `datetime` `logging` `hashlib` `hmac` `base64` `urllib` `time` `re` `typing` `abc` `dataclasses` |
| Must contain | One `BaseProvider` subclass with a non-empty `PROVIDER_NAME` |
| Must implement 6 methods | `resolve_name` `query_stock` `stock_in` `stock_out` `search` `get_today_statistics` |
| Optional | `query_batch` `move_batch_location` (base class has defaults returning a structured `not_implemented`) |

> **Parameter order matters**: connectivity tests call your methods **positionally** (e.g. `search("test", "material", None, None, None, False)`). You may rename parameters, but not reorder them — otherwise L1 fails outright.
>
> L2 really writes 1 unit of `test_item` into your system (`reason_category` is `other_in` / `other_out`) — **point it at a test environment**.

Since `os` is forbidden, read all configuration from the constructor's `config` dict (the Provider's `config` JSON in the DB). Don't try to read env vars or files.

### 3.3 Upload → test → activate

| Step | Endpoint | Notes |
|---|---|---|
| 1. Upload | `POST /api/erp/providers` (multipart `file`) | On pass, stored under `providers/custom/`; DB records `provider_name` / `class_name` / `filename`. Duplicate `provider_name` within a tenant → 409 |
| 2. Set config | `PUT /api/erp/providers/{id}` | Body `{name, config}`; `config` is arbitrary JSON passed straight to your constructor |
| 3. Level 1 test | `POST /api/erp/providers/{id}/test?level=1` | **Read-only**: `resolve_name` / `query_stock` / `search` / `get_today_statistics`, plus the three optional discovery methods `list_tenants` / `list_warehouses` / `list_users` (**not implementing them is not a failure** — they are marked `skipped`; if implemented, `{success, items}` is validated). Checks required keys, records per-method latency |
| 4. Level 2 test | `POST /api/erp/providers/{id}/test?level=2` | **Writes**: `stock_in` / `stock_out` — writes 1 unit of `test_item` each into your ERP. Point at a test environment |
| 5. Activate | `POST /api/erp/providers/{id}/activate` | **Requires L1 all-green**, else 400. Other Providers in the same tenant are auto-deactivated (single-active) |
| 6. Switch mode | `PUT /api/system/mode` → `external_erp` | The whole system now goes through your ERP |
| — | `GET /api/erp/providers/{id}/status` | Live probe using `get_today_statistics()`; returns `{online, latency_ms, error}` |
| — | `POST /api/erp/providers/{id}/deactivate` | Deactivate |

L1/L2 results are stored separately under `test_results.level1` / `.level2`; only an L1 pass writes `test_passed_at`. All operations require `ERP:ADMIN` and are scoped by `tenant_id` — cross-tenant access returns 403.

### 3.4 How the MCP side picks up your Provider

At startup `warehouse_mcp.py` does not read the database. It calls `GET /api/erp/providers/active-for-mcp`, and the backend scopes the result by the `tenant_id` derived from the API key (this removed an earlier cross-tenant leak from raw sqlite queries).

Contract:

- mode `self_owned` → use `DefaultProvider`;
- `external_erp` with an active Provider → dynamically load `providers/custom/<filename>` with config `{**config.yml, **DB config}`;
- **any failure falls back to `DefaultProvider`** (network error, 404, missing file, load exception), with a warning logged. So when "I switched to my ERP but data still lands in the local DB," the first thing to check is the fallback warning in the MCP log.

> **Multi-tenant path (fixed 2026-08)**: uploads are stored tenant-isolated at
> `providers/custom/<tenant_id>/<filename>`, while the MCP loader used to look only at
> the flat `providers/custom/<filename>` — so under multi-tenancy "upload + activate"
> silently fell back to the default Provider. `active-for-mcp` now returns `tenant_id`
> and the loader resolves "tenant subdirectory → flat path", supporting both layouts and
> logging every candidate path it tried when nothing is found. **Mind the image version**:
> older images lack this fix and need the file at the flat path.

### 3.5 External scope binding (needed for multi-warehouse / multi-org)

Once an external ERP is bound, **our tenants/warehouses have no relationship to yours**.
Rather than mirroring your org structure locally (dual maintenance, guaranteed drift),
the Provider reports what *your* system has, the user picks it while configuring an agent,
and we store the **raw codes** and pass them back verbatim on every call.

The three optional discovery methods are documented in
[WMS_Provider_Development.md](../docs/WMS_Provider_Development.md).
Implement whichever match your system — **implementing none still works** (the UI falls
back to manual code entry):

| Your system | Implement | Agent config UI |
|---|---|---|
| Single org, single warehouse | none | Leave both blank; calls use the fixed values in your Provider config |
| Single org, multiple warehouses | `list_warehouses` | Tenant is a text box (leave empty), warehouse is a dropdown |
| Multiple orgs and warehouses | both | Two-level cascade |

When only one candidate is returned the UI auto-selects it — no user action needed.

Selected values are stored on `mcp_connections.external_tenant_id` /
`external_warehouse_id` and injected into the Provider `config` at runtime:

```python
def __init__(self, config: dict):
    super().__init__(config)
    self.tenant_id = config.get("external_tenant_id") or config.get("tenant_id")
    self.warehouse_id = config.get("external_warehouse_id") or config.get("warehouse_id", "default")
```

There is **one Provider instance per agent**, so agents bound to different warehouses are
isolated automatically.

Endpoints: `GET /api/erp/external/tenants`, `GET /api/erp/external/warehouses`,
`GET /api/erp/external/users` — all require **`ERP:ADMIN`** (they use stored credentials
to reach your system and enumerate its org structure, so they are not open to read-only roles).

**The discovery outcome always lives in the body** (HTTP 200) — a provider not implementing
discovery is an expected path, not an HTTP error. **Unmet preconditions still return error
status codes** though: 400 (global admin did not specify `tenant_id`), 403 (insufficient
permission), 404 (no active provider for the tenant, or the provider file is missing),
500 (provider file failed to load). Possible `error` values:

| error | Meaning | UI behaviour |
|---|---|---|
| (none, `success: true`) | Discovery succeeded | Render dropdown |
| `not_implemented` | Provider doesn't implement the method | Fall back to manual entry |
| `probe_failed` | Provider raised an exception | Report failure, fall back to manual entry |
| `bad_response` | Provider returned something that is not a dict | Same as above |
| `probe_timeout` | No response within 20 seconds | Ask you to check your system's latency |
| `probe_busy` | Concurrent probes exhausted (max 4), or earlier probes still stuck in your system | Ask the user to retry later |

> Why `probe_busy` happens: discovery calls your code synchronously, so we run it on a
> separate daemon thread with a hard 20-second timeout and a concurrency cap of 4.
> **A timeout only stops us waiting — the stuck thread cannot be reclaimed.** If your endpoint
> hangs, those slots stay occupied until the calls actually finish. Please make discovery
> return within seconds.

**Where the tenant comes from for a global admin**: a normal user's tenant is decided by
their login (the frontend neither sends nor should send it). A global admin (`tenant_id`
NULL) has access to every tenant, so it is derived from the **warehouse selected in the
top-right**; with "All warehouses" selected no tenant can be derived and the UI asks them
to pick a specific warehouse first.

### 3.6 Identity import: authorization always stays on our side

**This cannot be delegated.** Who may log into our system, configure which agent, or change
face rules is decided by our `users(role, tenant_id)` + `user_warehouses` chain. Even though
inventory lives entirely in your system, the "user → tenant/role" mapping must exist on ours,
or the whole permission model is empty.

Import merely saves an admin from retyping your user table. **Two sources, either works**:

| Source | What you must do | Notes |
|---|---|---|
| Provider discovery | Implement `list_users()` | Click "Probe External System" in the UI |
| Bring your own JSON | **nothing at all** | Paste or upload a file; the import endpoint is pure persistence and does not touch the Provider |

JSON format (bare array, `{items:[...]}`, or `{users:[...]}` are all accepted):

```json
[
  {"id": "u1001", "name": "zhangsan", "display_name": "Zhang San",
   "warehouses": ["WH-BJ-01", "WH-BJ-02"]},
  {"id": "u1002", "name": "lisi", "warehouses": ["WH-BJ-01"]}
]
```

| Field | Required | Notes |
|---|---|---|
| `id` | ✅ | Dedup key. **Must be stable** — if it changes on your side, re-importing creates duplicates instead of updating |
| `name` | ✅ | Login name |
| `display_name` | | Display name |
| `warehouses` | | Warehouse codes this account can access **in your system** |

**Supplying `warehouses` is strongly recommended.** For non-admin roles we honour only
explicit warehouse grants (`user_warehouses`); without them an imported user logs in to an
**empty warehouse list and can do almost nothing**, and an admin has to grant them one by one.

At import time these codes are mapped to local warehouse anchors (import them first via
`POST /api/erp/external/import/warehouses`). Codes with no matching anchor are never dropped
silently — they come back in `unmatched_warehouses` (plus `unmatched_details`, which names the
tenant and account each unmatched code belongs to). The `admin` role gets no per-warehouse
grants: it can already see every warehouse in its tenant.

**Omitting the field is not the same as sending an empty array:**

| Form | Behaviour |
|---|---|
| no `warehouses` key | leaves the user's existing grants untouched |
| `"warehouses": []` | explicitly revokes all of the user's grants |
| `"warehouses": ["WH-A"]` | replaces the set (delete-then-insert) |

The distinction matters because grants are replaced wholesale. If "absent" meant "empty", an
incremental import that only refreshes display names would silently wipe every grant an admin
had added by hand on our side, with nothing in the response to indicate it.

What you do **not** need to supply:

| | Why |
|---|---|
| Passwords | Managed locally on our side; you expose no auth endpoint |
| Roles | You don't know our permission model — our admin assigns them per row at import time |
| Tenant membership | Same; decided at import time |

UI: **Settings → Data Management → "Import Identities from External System"**
(only shown in `external_erp` mode). Endpoint: `POST /api/erp/external/import/users`,
requires `USERS:ADMIN`.

**Two ways to say which tenant to import into:**

```jsonc
// (1) Tenant admin: omit it — the tenant comes from their login
{"default_password": "...", "users": [ ... ]}

// (2) Global admin: one tenant for the whole batch
{"default_password": "...", "tenant_id": 2, "users": [ ... ]}

// (3) Global admin: one call, accounts from different orgs into different tenants
{"default_password": "...", "users": [
  {"id": "u1001", "name": "zhangsan", "tenant_id": 2},
  {"id": "u1003", "name": "wangwu",   "tenant_id": 3}
]}
```

A tenant admin naming a different tenant gets **403** (no importing into someone else's
tenant); a global admin providing neither gets **400** (nothing to infer the tenant from).

> Note: **your orgs play no part in choosing our tenant.** Accounts discovered under
> `ORG-SH` still land in whatever `tenant_id` says. To map "your two orgs → our two tenants",
> use form (3) above.

**Uniqueness constraint**: a composite unique index on `(tenant_id, external_user_id)`
enforces this at the database level — import is check-then-insert, which the application layer
alone cannot make safe against concurrent submissions. Different tenants may share the same
external account ID (composite key); manually created local users have a NULL
`external_user_id`, do not participate in uniqueness, and are unaffected. Warehouse anchors
are constrained the same way on `(tenant_id, external_warehouse_id)`.

> **Upgrade note**: if duplicate external mappings already exist, the migration **deletes
> nothing** (these tables are referenced by stock records and others via foreign keys). It skips
> index creation and prints the duplicates plus remediation steps to the deploy log. After
> resolving them you must run the printed `CREATE UNIQUE INDEX` statement **manually** —
> re-running `alembic upgrade head` is a no-op.
>
> **Until the duplicates are resolved it is not merely "one less concurrency guard"**: import
> looks up an existing record by `external_user_id` and takes the first match, so which of the
> duplicate rows gets updated is undefined. Resolve them first.

Idempotency:

- Same `external_user_id` again → **updates** username/display_name/role, **leaves the password alone**
- A local user with the same name but a different external id → **skipped and reported**, never overwritten (this notably protects the local admin account)
- Manually created local users have an empty `external_user_id` and are unaffected

> **Imported users carry permissions only** — they are unrelated to the stock-movement
> `operator` (free-form text) and to face-library subjects (enrolled separately).
> Do not build implicit links between the three.

### 3.7 Face recognition scoping in external mode

Face rules are **warehouse-specific first, tenant-level as fallback**, keyed on **our**
`warehouse_id`:

- Your system **has** tenants → tenant-level rules suffice; create no local warehouses at all
- Your system has **no** tenants → the warehouse is the only scoping dimension, so import your
  warehouses as local rows **purely as permission anchors** (`user_warehouses` must bind a local
  `warehouse_id`); they carry no inventory

Endpoint: `POST /api/erp/external/import/warehouses`. Imported rows carry
`warehouses.external_warehouse_id`.

⚠️ **Agent binding must line up with the anchor.** Face rules hang off the local
`warehouse_id` while calls pass through `external_warehouse_id`. Picking them independently
produces "rule configured on Beijing, agent actually bound to Shanghai" — the rule
**silently does not apply, with no error**.

What the UI does: once an external warehouse is chosen, the local warehouse becomes
**derived and locked** and can no longer be changed on its own; if no local anchor exists yet,
one is created on save (reusing the idempotent import endpoint). To switch warehouses, change
the *external* warehouse dropdown and the local one follows. With no external warehouse
selected (single-warehouse systems, or discovery not implemented and the field left blank),
the local warehouse stays freely selectable — face rules then resolve against whichever local
warehouse the agent is bound to (warehouse-specific rule first, tenant-level as fallback).

**Creating connections directly via the API has none of this protection** — you must ensure
the row referenced by `warehouse_id` carries the same `external_warehouse_id` you send.

### 3.8 Local pages are empty in external mode

The dashboard, stock-movement records, inventory list, and product detail pages read **our
local database**, but in external mode all business data lives in your system — so these pages
will be empty. A banner at the top of each page explains this.

**This is not a fault.** Check your own system for the real data.

### 3.9 End-to-end example

A complete `AcmeWmsProvider` (all 6 methods plus matching `config.yml`) is in [WMS_Provider_Development.md](../docs/WMS_Provider_Development.md#wms-provider-development-guide).

---

## 4. Debugging

**Test a Provider standalone, without the full MCP stack:**

```bash
cd mcp
uv run python -c "
import yaml
from providers import load_provider
config = yaml.safe_load(open('config.yml'))
p = load_provider(config)
print(p.resolve_name('screw'))
print(p.query_stock('M3 Screw'))
print(p.get_today_statistics())
"
```

**Run the pre-upload validation and tests directly:**

```bash
uv run python -c "
from providers.validator import validate_provider_file
print(validate_provider_file('providers/custom/my_wms.py'))
"
uv run python -c "
from providers.test_runner import run_level1_tests
print(run_level1_tests('providers/custom/my_wms.py', {'api_base_url': 'https://...'}))
"
```

**Other:**

```bash
export LOG_LEVEL=DEBUG          # verbose logs
uv run python warehouse_mcp.py  # bare stdio server
npx @modelcontextprotocol/inspector uv run python warehouse_mcp.py   # GUI tool inspector
```

`mcp_pipe.py` protocol event logging is on by default (`MCP_PROTOCOL_EVENT_LOG=0` disables it); the log directory is set by `MCP_PIPE_LOG_DIR` (in containers, `/app/logs`).

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `未知的 provider 'xxx'` (unknown provider) | `PROVIDER_NAME` doesn't match `provider` in `config.yml` | Align spelling; confirm the `.py` sits under `providers/` or `providers/custom/` |
| Upload 400 "validation failed" | Hit a forbidden import/builtin, or missing methods | Walk the §3.2 table; the `errors` array lists every problem |
| L1 all-red with `Provider 加载失败` | Constructor raised (commonly: reading a missing config field) | Use `config.get(k, default)`, not `config[k]` |
| L1 method reports missing required field | Returned dict lacks a key | Add the required keys from §3.3 |
| Queries/inbound fine, **only outbound raises TypeError** | `stock_out` is missing `allow_partial_fallback` | Add it — the tool layer always passes it as a keyword |
| Activate returns 400 "pass Level 1 first" | L1 not run or not green | Run `POST .../test?level=1` |
| Switching to `external_erp` returns 400 | No active Provider | Activate one first |
| Switched to `external_erp` but data still hits the local DB | MCP fell back | Check the warning in the MCP log; review the §3.4 |
| MCP stuck at `Connecting to WebSocket server...` | Corporate firewall blocking WSS / wrong endpoint | Test on a mobile hotspot; confirm the `wss://` prefix; set `HTTPS_PROXY` if required |
| Voice command doesn't trigger the tool | Tool name/docstring too vague, or process not restarted | Rewrite the docstring around intent; restart the MCP process |
| Connection drops on long responses (1009) | Frame size exceeded | Tighten `max_results`, trim returned fields |
| Every tool (queries included) returns `face_auth_denied:http_404` | The backend at `api_base_url` has no `/face/verify-mcp` | Apply one of the three fixes in §2.5 (usually Option 1: split the URLs) |
| Every tool returns `face_auth_denied:transport_error` | Gate address unreachable, or your `/face/verify-mcp` exceeds 18s | Check `api_base_url` connectivity; speed up the implementation |
| `actual_operator` is empty in stock records | Face recognition disabled or no rule requires it | Expected; configure a face rule if you need the audit trail |
| 401 Unauthorized | Bad `auth` block or expired API key | Recreate the key under Web UI → User Management → API Keys |
| `/face/verify-mcp` returns 403 | API key lacks `FACE:WRITE` | Use a key that has it |
| Inventory / records / dashboard pages are empty in external mode | The data is in your system; these pages read our local DB | **Not a fault** — see §3.8. Check your own system for the real data |
| Face rules configured but stock moves are never blocked | The agent's warehouse and the rule's warehouse don't match, so the rule silently doesn't apply | See the warning in §3.7. The UI links them automatically; if you create connections via the API, keep them consistent yourself |
| External tenant/warehouse are text boxes instead of dropdowns | The Provider doesn't implement the matching discovery method | Expected — type the codes manually, or implement the methods in §3.5 |
| Discovery returns "no active ERP Provider for this tenant" | No Provider activated yet | Upload and activate per §3.3, or use paste-JSON import instead (no Provider needed) |
| Some users missing after import | A local user with the same name but a different external account exists and was skipped for safety | Check the returned `skipped` array — each entry carries a reason |
| Imported users can't log in | Not using the initial password set at import time | Log in with that initial password, then change it |
| Discovery returns `probe_busy` | Concurrency exhausted (max 4), or earlier probes are still stuck in your system | Retry later; if it persists, check whether your discovery endpoint hangs (stuck threads can't be reclaimed — slots free up only when those calls actually finish) |
| Discovery returns `probe_timeout` | Your system did not respond within 20s | Make discovery return within seconds |
| Deploy log says "skipped unique index" | Duplicate external mappings existed before the upgrade | The migration deletes nothing. Use the SQL in the log to inspect and resolve duplicates, then run the printed CREATE UNIQUE INDEX **manually** — re-running upgrade is a no-op |
| Global admin gets "pick a specific warehouse first" | "All warehouses" is selected, so no tenant can be derived | Pick a specific warehouse in the top-right; the tenant is derived from it |
| Re-importing created duplicate users | The external account `id` is not stable (the dedup key changed) | Ask for a stable account ID — see §3.6 |

## Related docs

- [MCP_README_EN.md](MCP_README_EN.md) — tool-level response field reference
- [../docs/WMS_Provider_Development.md](../docs/WMS_Provider_Development.md) — Provider interface contract and full example (bilingual)
- [../docs/MCP_External_System_Integration.md](../docs/MCP_External_System_Integration.md) — device-side onboarding from scratch (incl. getting a SenseCraft endpoint)
- [../docs/MCP_Server_Development_Practice.md](../docs/MCP_Server_Development_Practice.md) — MCP server development practices
- [MCP_SLIM_DESIGN.md](MCP_SLIM_DESIGN.md) — tool-set slimming and token budget design
- [../docs/CLAUDE_DESKTOP_CONFIG.md](../docs/CLAUDE_DESKTOP_CONFIG.md) — Claude Desktop configuration
