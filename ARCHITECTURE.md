# Sentinel Architecture & Implementation Details

Complete technical documentation of the sentinel-monitor system: architecture, data flow, algorithms, config schema, setup, and operational workflows.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────┐
│           sentinel-config.json                   │
│   (source of truth — NEVER overwritten)          │
│   candidates, endpoints, jobs, quality tiers     │
└────────────────────┬────────────────────────────┘
                     │  read-only
                     ▼
┌─────────────────────────────────────────────────┐
│              sentinel.py                         │
│                                                  │
│  1. Load config from sentinel-config.json        │
│  2. Test each candidate via curl chat completion  │
│  3. Auto-discover providers from config.yaml     │
│  4. Assign best model per job (quality+cost+lat) │
│  5. Write results to sentinel-output.json        │
│  6. Print health report (stdout)                 │
└────────────────────┬────────────────────────────┘
                     │  write
                     ▼
┌─────────────────────────────────────────────────┐
│           sentinel-output.json                   │
│   (health check results, overwritten each run)   │
│   available[], failed[], assignments[],          │
│   cron_updates[]                                 │
└────────────────────┬────────────────────────────┘
                     │  cron job reads this
                     ▼
┌─────────────────────────────────────────────────┐
│         Sentinel Cron Job                        │
│   Every hour: run sentinel.py, read output,     │
│   apply cronjob(action=update) for each change,  │
│   deliver health report to user                  │
└─────────────────────────────────────────────────┘
```

### Key Design Decision: Config Separation

**Problem discovered**: The original design overwrote the source config file with health check output. This destroyed candidate lists, provider endpoints, and job registry on the first run. The second run found empty config and failed — a cascading wipe failure.

**Solution**: Two separate files:
- `sentinel-config.json` — **source of truth**, read-only, manually configured or via `setup.py`
- `sentinel-output.json` — health check results, overwritten each run

The Python script reads `sentinel-config.json` via `load_config()` and writes results to `sentinel-output.json`. These paths are defined as constants at module level and never reassigned.

---

## 2. Configuration Schema

### File: `~/.hermes/sentinel-config.json`

```json
{
  "candidates": [
    {
      "provider": "sml-gateway",       // Provider identifier (must match an entry in provider_endpoints)
      "model": "sml/tools",            // Model ID as the provider routes it
      "cost": "free",                  // "free" | "paid-subscription" | "paid-per-use"
      "quality": "tier-2",            // "tier-1" | "tier-2" | "tier-3"
      "job_types": ["all"]            // ["all"] or ["research","builder",...]
    }
  ],

  "provider_endpoints": {
    "sml-gateway": {
      "url": "http://localhost:3334/v1/chat/completions",
      "key_source": "none"           // "config" | "none" | "gateway"
    },
    "modelark": {
      "url": "https://ark.ap-southeast.bytepluses.com/api/coding/v3/chat/completions",
      "key_source": "config"
    }
  },

  "job_registry": {
    "054722cb319b": {
      "name": "Daily Vault Ingest+LINT",  // Human-readable name
      "type": "builder"                   // Job type (must match a quality_requirements key)
    }
  },

  "quality_requirements": {
    "research": "tier-1",     // Minimum quality tier for this job type
    "builder": "tier-1",
    "content": "tier-1",
    "marketing": "tier-2",
    "design": "tier-2",
    "finance": "tier-2",
    "system": "tier-3",
    "growth": "tier-3"
  },

  "cost_priority": ["free", "paid-subscription", "paid-per-use"],
  "check_interval": "1 hour"
}
```

### Config Fields Detail

| Field | Required | Description |
|---|---|---|
| `candidates[]` | Yes | List of provider/model pairs to health-check each run |
| `provider_endpoints` | Yes | Maps provider name → endpoint URL + key source |
| `job_registry` | No | Maps job ID → name + type. Auto-populated by `setup.py` |
| `quality_requirements` | No | Minimum tier per job type. Has sensible defaults |
| `cost_priority` | No | Priority order. Default: free > subscription > per-use |
| `check_interval` | No | Display only, used in report footer. Default: "1 hour" |

### Candidate Properties

| Property | Values | Meaning |
|---|---|---|
| `cost: "free"` | No money spent on these API calls. Always preferred if quality matches. |
| `cost: "paid-subscription"` | You pay a flat monthly fee already. Marginal cost is zero. Use freely to fill quality gaps that free providers can't cover. |
| `cost: "paid-per-use"` | Pay per token. Only assigned when no free/subscription option meets the quality requirement. |
| `quality: "tier-1"` | Strong reasoning, long context, full tool calling. For research, builder, content jobs. |
| `quality: "tier-2"` | Good reasoning, tool calling, 32K+ context. For marketing, design, finance. |
| `quality: "tier-3"` | Fast response, basic reasoning. For system monitoring, growth. |

### Key Source Types (`provider_endpoints.key_source`)

| Value | Behavior |
|---|---|
| `"config"` | Look up the API key from `~/.hermes/config.yaml` under `custom_providers[].api_key` where `custom_providers[].name` matches the provider name |
| `"none"` | No API key needed. Test with no Authorization header. Common for local gateways. |
| `"gateway"` | Key managed by the Hermes Agent gateway. Skip from direct curl test — the provider's health is indirectly verified by cron jobs that use it. |

### Provider Endpoint Discovery

If `provider_endpoints` is missing an entry for a candidate's provider, sentinel falls back gracefully:
- No URL → provider is skipped from testing (logged as "gateway/no-url")
- No key_source → defaults to "gateway" (skipped)

---

## 3. Provider Testing Algorithm

### The Test Request

Sentinel tests each provider with a **minimal chat completion request**:

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}" \
  -m 15 <endpoint_url> \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <key>" \
  -d '{"model":"<model_id>","messages":[{"role":"user","content":"ping"}],"max_tokens":2}'
```

This is a **real API call**, not a `/health` endpoint check. It validates:
1. Server is reachable
2. Auth token is valid
3. Account has sufficient balance
4. The specific model exists and is accessible
5. Latency measurement

Cost per test: ~5 tokens (pilot + completion), effectively zero.

### HTTP Status Code Classification

| HTTP Code | Sentinel Status | Meaning |
|---|---|---|
| 200 | `ok` | Provider is fully working |
| 429 | `rate_limited` | Rate limit hit — provider is up but throttling |
| 402 | `insufficient_funds` | Account balance depleted |
| 404 | `model_not_found` | The specified model ID doesn't exist on this provider |
| 401, 403 | `auth_failed` | API key invalid or missing |
| 0 | `timeout` | Curl timed out (network issue, server unreachable) |
| 5xx | `server_error` | Server-side error on the provider |
| Other codes | `server_error` | Unrecognized error, treated as server error |

### Timeout Behavior

- Curl timeout: 15 seconds (`-m 15`)
- Subprocess timeout: 20 seconds (timeout + 5 second buffer)
- Total per-candidate test time: ~15s if healthy, ~20s if timeout

### Step 1 Execution Flow

```
For each candidate in candidates[]:
  1. Look up endpoint URL and key_source from provider_endpoints
  2. If key_source == "gateway" or URL is empty:
       → Skip (gateway/no-url), log as "⚙ skipped"
  3. If key_source == "config":
       → Load Hermes config.yaml
       → Find custom_providers[].name matching candidate.provider
       → Extract API key
  4. Execute curl test
  5. Classify response by HTTP code
  6. If "ok":
       → Add to available[] with provider, model, cost, quality, latency_ms, timestamp
  7. Else:
       → Add to failed[] with provider, model, status, http_code
```

### Step 1b: Auto-Discover Providers

After testing configured candidates, sentinel scans `~/.hermes/config.yaml` for `custom_providers` that are NOT already in the candidate list:

```
For each custom_provider in config.yaml:
  If provider name is already tested:
    → Skip (already covered)
  Fetch /v1/models endpoint to get available models
  Test first 3 models found
  If any respond HTTP 200:
    → Add to available[] as discovered (cost: free, quality: tier-2, job_types: all)
```

This catches providers that Hermes Agent uses but weren't explicitly configured in sentinel-config.json.

---

## 4. Model Assignment Algorithm

### Goal

For each cron job in the job registry, select the **best available model** that:
1. **Meets quality requirement** (preferred, but can accept lower tier if nothing better exists)
2. Has the **lowest cost** (free > subscription > per-use)
3. Has the **fastest latency** (tiebreaker)

### Rank Function

The core ranking logic sorts the candidate pool for each job:

```python
TIERS  = {"tier-1": 1, "tier-2": 2, "tier-3": 3}
COSTS  = {"free": 0, "paid-subscription": 1, "paid-per-use": 2}

def rank(candidate, job_type):
    req_tier = quality_requirements[job_type]  # e.g. "tier-1"
    cand_tier = candidate.quality                # e.g. "tier-2"

    # Priority 1: Does quality meet requirement? (lower = better)
    quality_ok = TIERS[cand_tier] <= TIERS[req_tier]

    # Priority 2: Cost tier (lower = cheaper = better)
    cost_order = COSTS[candidate.cost]

    # Priority 3: Latency (lower = faster = better)
    latency = candidate.latency_ms

    # Return tuple for Python sort (ascending)
    return (not quality_ok, cost_order, latency)
```

### Sorting Behavior

Python's `sorted()` sorts tuples lexicographically. This means:

1. First element `(not quality_ok)`: Candidates that **DO** meet the requirement get `False` (0). Candidates that don't get `True` (1). So quality-matching candidates sort first.

2. Second element `cost_order`: Among candidates with the same quality match, free (0) sorts before subscription (1), which sorts before per-use (2).

3. Third element `latency_ms`: Among candidates with same quality match and cost tier, faster (lower ms) sorts first.

### Example Walkthrough

Given:
- Job type: "research" (requires tier-1)
- Available models:
  - sml/tools (free, tier-2, 351ms)
  - sml/auto (free, tier-3, 292ms)
  - ModelArk/seed-2-0-pro (sub, tier-1, 1623ms)
  - ModelArk/gpt-oss-120b (sub, tier-1, 1448ms)

Filter by job_types: If "all" or contains "research", all pass.

Rank tuples:

| Candidate | quality_ok | not quality_ok | cost | latency | Rank tuple |
|---|---|---|---|---|---|
| sml/tools | False (tier-2 > tier-1) | True (1) | 0 | 351 | (1, 0, 351) |
| sml/auto | False (tier-3 > tier-1) | True (1) | 0 | 292 | (1, 0, 292) |
| seed-2-0-pro | True (tier-1 <= tier-1) | False (0) | 1 | 1623 | (0, 1, 1623) |
| gpt-oss-120b | True (tier-1 <= tier-1) | False (0) | 1 | 1448 | (0, 1, 1448) |

**Winner**: ModelArk/gpt-oss-120b (rank 0,1,1448 — best quality match with fastest latency among subscription models).

### Quality Mismatch Warning

If the best available candidate does NOT meet the quality requirement, sentinel marks it with "⚠" in the assignment output. This indicates a quality degradation that will persist until the originally qualified provider recovers.

### Unassigned Jobs

If no available candidate supports the job type (all matching candidates are down), the job is left unassigned with a "❌ NO available model" message. The original job configuration is NOT modified — it simply won't be updated by sentinel.

---

## 5. Output Format

### File: `~/.hermes/sentinel-output.json`

```json
{
  "last_tested_at": "2026-05-13T05:09:01.493419+00:00",
  "available": [
    {
      "provider": "sml-gateway",
      "model": "sml/tools",
      "cost": "free",
      "quality": "tier-2",
      "latency_ms": 351
    }
  ],
  "failed": [
    {
      "provider": "openrouter",
      "model": "claude-sonnet-4",
      "status": "server_error"
    }
  ],
  "discovered": [],
  "assignments": {
    "054722cb319b": {
      "provider": "ModelArk",
      "model": "gpt-oss-120b-250805",
      "cost": "paid-subscription",
      "quality": "tier-1"
    }
  },
  "cron_updates": [
    {
      "job_id": "054722cb319b",
      "provider": "ModelArk",
      "model": "gpt-oss-120b-250805"
    }
  ]
}
```

### Output Sections

| Key | Type | Description |
|---|---|---|
| `last_tested_at` | ISO 8601 string | When this health check ran |
| `available[]` | Array | Provider/model pairs that responded HTTP 200 |
| `failed[]` | Array | Provider/model pairs that failed (status + HTTP code) |
| `discovered[]` | Array | Providers found via config.yaml auto-discovery (not in sentinel-config.json) |
| `assignments{} | Object | Job ID → assigned provider/model/cost/quality for every job |
| `cron_updates[]` | Array | Subset of assignments where model changed — these are the cronjob() calls the cron agent should execute |

### Stdout (Human Report)

Printed during script execution and consumed by the cron job for delivery:

```
🛡 Sentinel Provider Health Check
Timestamp: 2026-05-13T05:09:01.493419+00:00

━━━ Provider Status ━━━
✅ FREE:
   sml-gateway/sml/tools (tier-2, 351ms)
✅ PAID-SUBSCRIPTION:
   ModelArk/gpt-oss-120b-250805 (tier-1, 1448ms)
❌ DOWN:
   openrouter/claude-sonnet-4 (insufficient_funds)

━━━ Assignments ━━━
• Free providers: 2 jobs
• Subscription providers: 3 jobs
• Cron job updates needed: 5

━━━ Updates ━━━
• Daily Vault → ModelArk/gpt-oss-120b-250805
• Space Daily Report → ModelArk/gpt-oss-120b-250805
• Space 6-Hour Log → sml-gateway/sml/auto

Next check: 1 hour
```

---

## 6. Sentinel Cron Job

### Creation

In Hermes Agent's `cronjob()` tool:

```python
cronjob(
    action='create',
    name='Sentinel Monitor',
    prompt="""You are Sentinel — LLM Provider Health Monitor.
    
    1. Run: python3 ~/.hermes/skills/sentinel-monitor/scripts/sentinel.py
    2. Read sentinel-output.json for health data and cron_updates.
    3. For each item in cron_updates, call cronjob(action='update', job_id='...', model={"provider":"...", "model":"..."}).
    4. Deliver the health report to the user.""",
    schedule='0 * * * *',          # Every hour, on the hour
    enabled_toolsets=['terminal', 'file', 'cronjob'],
    workdir='/path/to/your/workspace'  # Optional
)
```

### What the Cron Agent Does Each Hour

1. **Execute sentinel.py** — The Python script runs, tests all providers, builds assignments, writes sentinel-output.json
2. **Read sentinel-output.json** — Parse the JSON to extract `cron_updates[]`.
3. **Apply cron updates** — For each update item, call `cronjob(action='update', job_id=update.job_id, model={"provider": update.provider, "model": update.model}).`
4. **Deliver report** — Print the health summary so the Hermes delivery system sends it to the user's connected platform (Discord, Telegram, etc.)

### Cron Update Logic

- Only jobs that need to change are updated. If a job's current model is still the best option, sentinel does NOT touch it.
- The update only changes `provider` and `model` -- it preserves `schedule`, `repeat`, `deliver`, and other job settings.
- When a previously-down provider recovers, sentinel will re-qualify it and may route jobs back (if it provides better quality or cost).
- **Idempotent**: Running sentinel hourly is safe. If the config hasn't changed, `cron_updates` may be empty and no updates are sent.

### Error Recovery

If sentinel.py fails during execution:
- The script still prints an error message and writes sentinel-output.json with what it could gather.
- The cron job's prompt instructs the agent to "deliver the report even if incomplete."
- No cron updates are applied if sentinel didn't produce valid assignments.
- The next run (1 hour later) will attempt again from scratch.

---

## 7. Setup Workflow (setup.py)

`setup.py` is an interactive Python wizard that guides the user through configuration:

### Step 1: Provider Config

Users can enter providers in either format:

**Pipe-separated format:**
```
Provider name | Model | Cost | Tier | API URL | Key source
sml-gateway | sml/tools | free | 2 | http://localhost:3334/v1/chat/completions | none
ModelArk | gpt-oss-120b-250805 | paid-subscription | 1 | https://... | config
```

**Natural language:**
```
I have ModelArk with gpt-oss-120b, paid-subscription, tier-1
```
The wizard asks follow-up questions (endpoint URL, key source) to complete the config.

### Step 2: Cron Job Discovery

The wizard attempts to discover existing cron jobs:
```bash
hermes cron list --format=json
```
Each found job is added to `job_registry` with its ID, name, and type. The user can also skip this and enter jobs manually.

### Step 3: Quality Requirements

Defaults are applied for 8 job types. The user can accept or customize:

```
research  → tier-1
builder   → tier-1
content   → tier-1
marketing → tier-2
design    → tier-2
finance   → tier-2
system    → tier-3
growth    → tier-3
```

### Step 4: Check Interval

How often the cron job should run. Default: "1 hour" (maps to `0 * * * *`).

### Step 5: Write Config

The assembled configuration is written to `~/.hermes/sentinel-config.json` with permissions `0600` (owner read/write only), then the final config is printed for review.

---

## 8. Uninstall Workflow

`uninstall.sh` removes sentinel in this order:

1. Find and remove the Sentinel cron job (via `hermes cron list | grep -i "sentinel" | hermes cron remove`)
2. Prompt: delete `~/.hermes/sentinel-config.json`? (defaults to "keep")
3. Delete `~/.hermes/sentinel-output.json`
4. Keep the skill directory at `~/.hermes/skills/sentinel-monitor/` (can be re-used for reinstall)

Note: The cron job removal only removes jobs whose name contains "sentinel" (case-insensitive). If there are multiple sentinel jobs, they are all removed.

---

## 9. File Structure

```
sentinel-monitor/
├── scripts/
│   └── sentinel.py              # Main health monitor (~248 lines)
├── setup.py                     # Interactive configuration wizard
├── install.sh                   # Wrapper: calls setup.py
├── uninstall.sh                 # Clean removal script
├── SKILL.md                     # Hermes Agent skill loading metadata
├── README.md                    # User-facing documentation (what + why)
├── ARCHITECTURE.md              # This file (how + deep technical detail)
├── references/
│   └── pitfalls.md              # Known issues and gotchas
└── templates/
    └── sentinel-config.example.json  # Starter config template
```

---

## 10. Project Structure

```
/home/anuntachai/.hermes/
├── config.yaml                  # Hermes Agent config (API keys, providers).
├── sentinel-config.json          # Sentinel source config (read by sentinel.py)
├── sentinel-output.json          # Health check results (written by sentinel.py)
└── skills/
    └── sentinel-monitor/
        ├── scripts/
        │   └── sentinel.py       # The health monitor script
        ├── setup.py              # Interactive setup wizard
        ├── install.sh            # Wrapper → setup.py
        ├── uninstall.sh          # Uninstall script
        ├── SKILL.md              # Skill metadata
        ├── README.md             # User guide
        ├── ARCHITECTURE.md       # This file — architecture & implementation
        ├── references/
        │   └── pitfalls.md       # Known issues
        └── templates/
            └── sentinel-config.example.json  # Starter config
```

---

## 11. Cron Job Integration

Sentinel runs as a Hermes Agent cron job, scheduled hourly:

```
┌─────────────────────────┐
│   Sentinel Cron Job     │
│   Schedule: 0 * * * *   │
│   Toolsets: terminal,   │
│             file,       │
│             cronjob     │
└───────────┬─────────────┘
            │ Every hour, the agent:
            │
            ▼
┌──────────────────────────────────────┐
│  1. Run sentinel.py                  │
│  2. Read sentinel-output.json        │
│  3. For each cron_update:            │
│       cronjob(action=update)         │
│  4. Deliver health report to user    │
└──────────────────────────────────────┘
```

### Cron Update Flow

When sentinel detects a provider failure, it writes `cron_updates` with the replacement model for each job. The cron job agent executes:

```python
cronjob(
    action='update',
    job_id='abc123',
    model={'provider': 'ModelArk', 'model': 'gpt-oss-120b-250805'}
)
```

This changes the model for that cron job — keeping schedule, repeat, deliver, and other settings intact.

### Recovery Flow

When a previously failed provider recovers:
1. Next sentinel run detects it via HTTP 200
2. Sentinel re-runs the assignment algorithm
3. If the recovered provider provides better quality/cost/latency, jobs are routed back
4. `cron_updates` is written and applied

---

## 12. Error Modes & Recovery

### Provider-Side Errors

| Error | Cause | Recovery |
|---|---|---|
| HTTP 402 | Account ran out of balance | Add funds; sentinel will re-test next hour and restore if working |
| HTTP 401/403 | API key expired or changed | Refresh key in config.yaml; sentinel re-tests hourly |
| HTTP 429 | Rate limited | Sentinel marks as rate_limited; next run retries. If recovered, routes back. |
| HTTP 404 | Endpoint URL changed or model retired | Fix endpoint in sentinel-config.json |
| timeout (HTTP 0) | Provider server unreachable | Sentinel marks as timeout; next run retries |
| 5xx | Provider server error | Sentinel marks as server_error; next run retries |

### Sentinel-Side Errors

| Error | Cause | Recovery |
|---|---|---|
| Config file missing | sentinel-config.json deleted or moved | sentinel falls back to empty defaults. Run setup.py to reconfigure. |
| No candidates | Config exists but candidates list is empty | sentinel prints "No candidates" and exits. Edit config or run setup.py. |
| hermes cron list fails | hermes CLI not installed or not accessible | Sentinel skips cron status check. Health testing and output still work. |

### Cascading Failure Prevention

The two-file architecture (source config + output file) prevents cascading failures:
- **Old problem**: sentinel.py overwrote the config with health results, destroying candidates/endpoints on every run
- **Current design**: sentinel-config.json is NEVER modified by the script. Only sentinel-output.json is written.

---

## 13. Custom Provider Discovery

Sentinel auto-discovers providers from `~/.hermes/config.yaml` custom_providers:

```python
# After testing all configured candidates...
test