# Sentinel Implementation Details

Technical deep-dive: architecture, data flow, algorithms, config schema, cron integration, and operational workflows.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  sentinel-config.json  (READ-ONLY source)    │
│  candidates · endpoints · jobs · tiers       │
└──────────┬───────────────────────────────────┘
           │  read
           ▼
┌──────────────────────────────────────────────┐
│  sentinel.py  (health monitor)               │
│                                              │
│  Step 1: Test each candidate via curl        │
│    - POST to URL with {model, messages,      │
│      max_tokens: 2}                          │
│    - Classify by HTTP code: ok, 429, 402,    │
│      404, auth_failed, timeout, server_error  │
│  Step 1b: Discover custom_providers from     │
│    config.yaml not already tested             │
│    (fetch /models, test first 3)              │
│  Step 2: For each job, rank available models:│
│    1. quality >= requirement (preferred)      │
│    2. cost: free > sub > per-use              │
│    3. latency: fastest wins                   │
│  Write sentinel-output.json                  │
│  Print summary report                        │
└──────────┬───────────────────────────────────┘
           │  write
           ▼
┌──────────────────────────────────────────────┐
│  sentinel-output.json  (overwritten)         │
│  available[] · failed[] · assignments{}      │
│  cron_updates[] · last_tested_at             │
└──────────┬───────────────────────────────────┘
           │  cron agent reads
           ▼
┌──────────────────────────────────────────────┐
│  Cron job (hourly): run script, apply        │
│  cronjob(action=update) per cron_updates     │
└──────────────────────────────────────────────┘
```

### Key Design Decision: Config Separation

The critical architectural decision that prevents cascading failures:

| File | Role | Modified by sentinel.py? |
|---|---|---|
| `sentinel-config.json` | Source of truth: candidates, endpoints, jobs, tiers | **❌ NEVER** |
| `sentinel-output.json` | Health results: available[], failed[], assignments, updates | **✅ Yes** (overwrite) |
| `~/.hermes/config.yaml` | Hermes Agent config: custom_providers with API keys | **❌ NEVER** (read for keys) |

---

## Provider Testing Algorithm

Sentinel tests each candidate with a **real chat completion request**:

```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}" \
  -m 15 <URL> \
  -H "Content-Type: application/json" \
  [-H "Authorization: Bearer <key>"] \
  -d '{"model":"<id>","messages":[{"role":"user","content":"ping"}],"max_tokens":2}'
```

This validates:
1. Server is reachable (not just `/health`)
2. Auth is valid (if applicable)
3. Account has balance
4. Specific model is accessible
5. Latency

Cost: ~5 tokens per test (negligible).

### HTTP → Status Mapping

| HTTP Code | Status | Meaning |
|---|---|---|
| 200 | ok | Working |
| 429 | rate_limited | Rate limited |
| 402 | insufficient_funds | Balance depleted |
| 404 | model_not_found | Model ID wrong |
| 401/403 | auth_failed | Key invalid |
| 0 (curl timeout) | timeout | Network unreachable |
| 5xx/other | server_error | Server-side failure |

### Key Source Resolution

When `key_source` = "config":
1. Read `~/.hermes/config.yaml`
2. Find `custom_providers[].name` matching `candidate.provider`
3. Extract `custom_providers[].api_key`

When `key_source` = "gateway": Skip (provider is managed by Hermes gateway).
When `key_source` = "none": No auth header.

### Auto-Discovery

After testing configured candidates, sentinel discovers additional providers:
1. Read `custom_providers` from `~/.hermes/config.yaml`
2. Skip any already in `candidates`
3. For each new provider, fetch `<base_url>/v1/models`
4. Test first 3 models

---

## Model Assignment Algorithm

For each cron job, select the best available model using a **tuple sort**:

```python
TIERS  = {"tier-1": 1, "tier-2": 2, "tier-3": 3}
COSTS  = {"free": 0, "paid-subscription": 1, "paid-per-use": 2}

def rank(candidate, job_type):
    req = quality_requirements[job_type]   # e.g., "tier-1"
    cand = candidate.quality                # e.g., "tier-2"

    # Priority 1: Does quality meet the job's requirement?
    # False (0) sorts before True (1), so matching candidates win
    quality_ok = TIERS[cand] <= TIERS[req]

    # Priority 2: Cost (lower = cheaper)
    cost = COSTS.get(candidate.cost, 99)

    # Priority 3: Latency (lower = faster)
    latency = candidate.latency_ms

    return (not quality_ok, cost, latency)  # Sort ascending
```

Python sorts tuples left-to-right, so:
1. **quality_ok first**: Providers meeting the requirement sort first
2. **cost second**: Among same-quality, cheapest sorts first
3. **latency third**: Among same cost, fastest sorts first

### Example

Job: "research" (requires tier-1)

Available models:

| Model | Quality | Quality Match? | Cost | Latency | Rank |
|---|---|---|---|---|---|
| sml/tools | tier-2 | No (2 > 1) | 0 | 351 | (True, 0, 351) |
| sml/auto | tier-3 | No (3 > 1) | 0 | 292 | (True, 0, 292) |
| ModelArk/pro | tier-1 | Yes | 1 | 1623 | (False, 1, 1623) |
| ModelArk/gpt-oss | tier-1 | Yes | 1 | 1448 | (False, 1, 1448) |

Winner: **ModelArk/gpt-oss** — matches quality, subscription cost, fastest of the tier-1 options.

### Fallback Behavior

If no candidate meets the quality requirement, sentinel assigns the best available option but marks it ⚠️. If no candidate supports the job type at all, the job is left unassigned ❌.

---

## Configuration Schema

### sentinel-config.json

```json
{
  "candidates": [{"provider": "name", "model": "id", "cost": "free", "quality": "tier-N", "job_types": ["all"]}],
  "provider_endpoints": {"name": {"url": "https://...", "key_source": "config|none|gateway"}},
  "job_registry": {"job_id": {"name": "...", "type": "research"}},
  "quality_requirements": {"research": "tier-1", "builder": "tier-1", "content": "tier-1", "marketing": "tier-2", "design": "tier-2", "finance": "tier-2", "system": "tier-3", "growth": "tier-3"},
  "cost_priority": ["free", "paid-subscription", "paid-per-use"],
  "check_interval": "1 hour"
}
```

### Cost Types

| Type | When to Use |
|---|---|
| free | Zero cost — always preferred when quality matches |
| paid-subscription | Already paying monthly — use freely to fill quality gaps |
| paid-per-use | Pay per token — use as fallback only |

### Quality Tiers

| Tier | Purpose | Typical Jobs |
|---|---|---|
| tier-1 | Strong reasoning, long context, full tool use | research, builder, content |
| tier-2 | Good reasoning, 32K+ context | marketing, design, finance |
| tier-3 | Fast response, basic reasoning | system monitoring, growth |

### Provider Endpoint Key Sources

| key_source | Behavior |
|---|---|
| config | Look up key from `~/.hermes/config.yaml` → `custom_providers[].api_key` |
| none | No auth (local gateways, open APIs) |
| gateway | Managed by Hermes Agent — skip from direct test |

---

## Sentinel Cron Job Setup

### In Hermes Agent

```python
cronjob(
    action='create',
    name='Sentinel Monitor',
    prompt='''You are Sentinel — LLM Provider Health Monitor.

1. Run: python3 ~/.hermes/skills/sentinel-monitor/scripts/sentinel.py
2. Read sentinel-output.json for health data and cron_updates.
3. For each cron_update: cronjob(action='update', job_id='...', model={"provider":"...","model":"..."})
4. Deliver the health report to the user.''',
    schedule='0 * * * *',
    enabled_toolsets=['terminal', 'file', 'cronjob'],
    workdir='/path/to/your/workspace',
)
```

### Hourly Cycle

```
Hourly tick → subagent starts
  ↓
Step 1: Run sentinel.py → tests providers → writes sentinel-output.json
  ↓
Step 2: Read sentinel-output.json
  ↓
Step 3: If cron_updates[] is non-empty:
        For each: cronjob(action=update, job_id=..., model=...)
  ↓
Step 4: Print health report → auto-delivered to user
```

### Cron Update Behavior

- **Only changed models are updated**. If sentinel finds the same best model for a job that was assigned last run, `cron_updates` stays empty.
- **Original job is fully preserved**. The update only changes `provider` and `model` — schedule, repeat, deliver, skills, and toolsets remain untouched.
- **Idempotent**. Running hourly is safe. Duplicate updates are harmless.

### Recovery Flow

```
Provider X fails (HTTP 429/500/timeout)
  ↓
Next sentinel run: X marked as failed
  ↓
sentinel-output.json.cron_updates[] says: move jobs from X → Y
  ↓
Cron agent applies updates
  ↓
Jobs now use model Y
  ...
X recovers
  ↓
Next sentinel run: X marked as ok
  ↓
If X provides better quality/cost: cron_updates say move back
  ↓
Cron agent applies recovery updates
```

---

## Setup Process (setup.py)

`setup.py` is an interactive wizard that builds `sentinel-config.json`:

1. **Collect providers** — Users describe providers in pipe-separated format:
   ```
   provider | model | cost_type | quality_tier | endpoint_url | key_source
   ```
   Or enter naturally and answer follow-up prompts.

2. **Discover cron jobs** — Runs `hermes cron list` and auto-populates `job_registry`. Can also enter manually.

3. **Set quality requirements** — Accept defaults or customize per job type.

4. **Set check interval** — "1 hour" default.

5. **Write config** — Creates `~/.hermes/sentinel-config.json` with `0600` permissions.

---

## Output Format (sentinel-output.json)

```json
{
  "last_tested_at": "2026-05-13T05:09:01.493419+00:00",
  "available": [{"provider":"sml-gateway","model":"sml/tools","cost":"free","quality":"tier-2","latency_ms":351}],
  "failed": [{"provider":"openrouter","model":"claude-sonnet-4","status":"insufficient_funds"}],
  "discovered": [],
  "assignments": {"054722cb319b": {"provider":"ModelArk","model":"gpt-oss-120b-250805","cost":"paid-subscription","quality":"tier-1"}},
  "cron_updates": [{"job_id":"054722cb319b","provider":"ModelArk","model":"gpt-oss-120b-250805"}]
}
```

### Stdout Summary

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
```

---

## Error Modes

| Error | Cause | Recovery |
|---|---|---|
| HTTP 402 | Balance depleted | Add funds; sentinel auto-recovers next run |
| HTTP 401/403 | Key expired | Refresh key; sentinel auto-recovers |
| HTTP 429 | Rate limited | Back off; next run retries |
| timeout | Network failure | Next run retries |
| Config file missing | Deleted sentinel-config.json | Run setup.py |
| No candidates | Empty candidates list | Edit config or run setup.py |

### Cascading Prevention

The old design had sentinel.py overwrite `crispcraft-provider.conf` with health results, destroying the candidates/endpoints/job registry. Subsequent runs found empty config and produced empty assignments — a total cascade failure.

**Fix**: Source config (`sentinel-config.json`) is NEVER modified by the script. Results go to a separate file (`sentinel-output.json`). The Python code has no write path to the source config.

---

## File Structure

```
sentinel-monitor/
├── scripts/
│   └── sentinel.py          # Health monitor (~250 lines)
├── setup.py                 # Interactive configuration wizard
├── install.sh               # Wrapper: calls setup.py
├── uninstall.sh             # Removes cron job + config files
├── SKILL.md                 # Hermes Agent skill metadata
├── README.md                # User-facing guide (what + how to use)
├── ARCHITECTURE.md          # Architecture overview & data flow
├── IMPLEMENTATION.md        # This file — deep technical detail
├── references/
│   └── pitfalls.md          # Known issues & workarounds
├── templates/
│   └── sentinel-config.example.json  # Starter template
└── .git/                    # Version control
```

---

## Cron Job Integration Detail

The cron job is the **automation layer** — sentinel.py is the **worker**. Without the cron job, sentinel still works as a manual tool (just print to stdout and write output file). The cron job adds:

1. **Automatic scheduling** — runs hourly without human intervention
2. **Cron model updates** — automatically switches jobs to working models
3. **Report delivery** — sends health reports to Discord/Telegram

### Toolset Requirements

The cron job needs these toolsets:
- `terminal` → to run `python3 sentinel.py`
- `file` → to read `sentinel-output.json`
- `cronjob` → to update job models

### Model Assignment for the Cron Job

The cron job itself needs a working model to run. Use a free provider (sml-gateway/sml/tools) or a subscription provider (ModelArk/gpt-oss-120b) — anything that is currently passing health checks.

### Idempotency

Running sentinel hourly is safe because:
- The assignment algorithm is deterministic (same inputs → same outputs)
- `cron_updates[]` only includes changes (if model hasn't changed, no update is listed)
- `cronjob(action=update)` with the same provider/model is a no-op

---

## Operational Notes

### Adding a New Provider

1. Edit `sentinel-config.json`: add candidate entry + endpoint entry
2. Run `python3 sentinel.py` to test it
3. Sentinel will include it in future assignments if it's the best fit

### Removing a Provider

1. Remove candidate from `sentinel-config.json`
2. Run `python3 sentinel.py` — sentinel will re-assign using remaining providers
3. Cron job will apply updates to affected jobs

### Changing Quality Requirements

Edit `quality_requirements` in sentinel-config.json. Next sentinel run will recalculate assignments.

### Cost vs Quality Trade-offs

The algorithm always prioritizes **quality match first**, then cost. This means:

- A free tier-3 model will be chosen over a paid tier-1 model **only** for tier-3 jobs
- For tier-1 jobs, sentinel will use paid tier-1 models if no free tier-1 is available (correct behavior — quality must be met)
- If all tier-1 models are down, sentinel assigns the best tier-2 model and marks it ⚠️

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 1.0 | May 2026 | Initial CrispCraft-specific implementation |
| 2.0 | May 2026 | Generic rewrite, config separation bug fix, interactive setup, auto-discovery |
