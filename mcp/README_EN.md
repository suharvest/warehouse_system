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
| 3. Level 1 test | `POST /api/erp/providers/{id}/test?level=1` | **Read-only**: `resolve_name` / `query_stock` / `search` / `get_today_statistics`. Checks required keys, records per-method latency |
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

> **Known limitation (multi-tenant deployments)**: uploads are stored tenant-isolated at `providers/custom/<tenant_id>/<filename>`, but the MCP loader looks for `providers/custom/<filename>`. Single-tenant (`tenant_id` is None) paths match and work; multi-tenant falls back to the default Provider. Verify this path before relying on it in a multi-tenant environment.

### 3.5 End-to-end example

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
| Switched to `external_erp` but data still hits the local DB | MCP fell back | Check the warning in the MCP log; review the §3.4 known limitation |
| MCP stuck at `Connecting to WebSocket server...` | Corporate firewall blocking WSS / wrong endpoint | Test on a mobile hotspot; confirm the `wss://` prefix; set `HTTPS_PROXY` if required |
| Voice command doesn't trigger the tool | Tool name/docstring too vague, or process not restarted | Rewrite the docstring around intent; restart the MCP process |
| Connection drops on long responses (1009) | Frame size exceeded | Tighten `max_results`, trim returned fields |
| Every tool (queries included) returns `face_auth_denied:http_404` | The backend at `api_base_url` has no `/face/verify-mcp` | Apply one of the three fixes in §2.5 (usually Option 1: split the URLs) |
| Every tool returns `face_auth_denied:transport_error` | Gate address unreachable, or your `/face/verify-mcp` exceeds 18s | Check `api_base_url` connectivity; speed up the implementation |
| `actual_operator` is empty in stock records | Face recognition disabled or no rule requires it | Expected; configure a face rule if you need the audit trail |
| 401 Unauthorized | Bad `auth` block or expired API key | Recreate the key under Web UI → User Management → API Keys |
| `/face/verify-mcp` returns 403 | API key lacks `FACE:WRITE` | Use a key that has it |

## Related docs

- [MCP_README_EN.md](MCP_README_EN.md) — tool-level response field reference
- [../docs/WMS_Provider_Development.md](../docs/WMS_Provider_Development.md) — Provider interface contract and full example (bilingual)
- [../docs/MCP_External_System_Integration.md](../docs/MCP_External_System_Integration.md) — device-side onboarding from scratch (incl. getting a SenseCraft endpoint)
- [../docs/MCP_Server_Development_Practice.md](../docs/MCP_Server_Development_Practice.md) — MCP server development practices
- [MCP_SLIM_DESIGN.md](MCP_SLIM_DESIGN.md) — tool-set slimming and token budget design
- [../docs/CLAUDE_DESKTOP_CONFIG.md](../docs/CLAUDE_DESKTOP_CONFIG.md) — Claude Desktop configuration
