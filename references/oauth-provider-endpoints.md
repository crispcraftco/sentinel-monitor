# OAuth Provider Health Testing

When sentinel has no direct API key for a provider (OAuth-managed, Hermes-internal credentials, etc.), it can still verify the provider is alive through **endpoint liveness** testing — sending a minimal request to the provider's own URL without credentials.

## Testing Pattern

```bash
curl -s -m 30 -o /dev/null -w '%{http_code} %{time_total}' \
  https://inference-api.nousresearch.com/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek/deepseek-v4-flash","messages":[{"role":"user","content":"ping"}],"max_tokens":2}'
```

## HTTP Code Semantics (without authentication)

| Response | HTTP Code | Meaning | Sentinel Action |
|----------|-----------|---------|----------------|
| Fast response (400/401/403/404/405/422/501) | Any of these | Server is alive, parsing requests, responding. These are valid API responses from a live backend. | ✅ `gateway_ok` — provider reachable |
| Timeout (code 0) | 0 | Server unreachable, DNS failure, or network down | ❌ `timeout` — provider down |
| 502/503 | 502/503 | Infrastructure broken (load balancer/gateway error) | ❌ `server_error` — provider down |

## Key Nuance: 400 vs 404 at OAuth Providers

Testing `https://inference-api.nousresearch.com/v1/chat/completions`:

| Model name | Response | Meaning |
|-------------|----------|---------|
| `deepseek/deepseek-v4-flash` | `400: "Unknown model: deepseek/deepseek-v4-flash..."` | Request format valid, model name recognized by server, but authentication is missing. Server is fully operational. |
| `qwen3.6-plus` or anything else | `404: "Model 'X' not found..."` | Server alive but model string doesn't match any known model. |

The **400 with "Unknown model"** is the strongest signal without auth — it means the specific model exists in the server's registry, the API endpoint is processing the request, and the only thing blocking completion is missing credentials.

The **404** still confirms server availability but with a weaker signal (model name may be wrong or deprecated).

## /v1/models Public Endpoint

The Nous API's `/v1/models` endpoint returns a list of all available models (400+ models as of May 2026) **without authentication**. This can be used to:

1. Confirm a model still exists in the provider's catalog
2. Verify the model endpoint format hasn't changed
3. Discover new free models when Nous rotates their free tier
4. Cross-reference: if `/v1/models` lists model X AND `/chat/completions` responds (any code), the provider is operational

```bash
# Browse full catalog (public, no auth)
curl -s -m 15 https://inference-api.nousresearch.com/v1/models | python3 -m json.tool

# Check for specific models
curl -s https://inference-api.nousresearch.com/v1/models | python3 -c "import json,sys; [print(m['id']) for m in json.load(sys.stdin)['data'] if 'deepseek' in m['id']]"
```

### Nous Model Discovery Procedure

When a new free model appears on Nous:

1. **Browse catalog**: `curl -s https://inference-api.nousresearch.com/v1/models` — public, no auth needed
2. **Test via Hermes gateway** (direct API tokens get 401 — only Hermes can refresh OAuth):
   ```bash
   hermes chat -q 'Say hello in one sentence.' -m 'deepseek/deepseek-v4-flash' --provider nous
   ```
3. **Add to sentinel-config.json** with `key_source: "gateway"`:
   ```json
   {"provider": "nous", "model": "deepseek/deepseek-v4-flash", "cost": "free", "quality": "tier-1", "job_types": ["all"]}
   ```
4. **Run sentinel**: `python3 ~/.hermes/skills/sentinel-monitor/scripts/sentinel.py`
5. **Apply cron updates** from `~/.hermes/sentinel-output.json` → `cron_updates` array

### Why Direct API Calls Fail

The Nous OAuth token (`credential_pool.nous[].access_token` in `~/.hermes/auth.json`) is a device_code grant. It cannot be used directly via curl — gets 401. Only the Hermes gateway handles token refresh. For testing, always use `hermes chat` or sentinel's gateway liveness checks.

## CRITICAL: Never Test One Provider Through Another

`nous/qwen3.6-plus` is an OAuth-authenticated upstream provider at `inference-api.nousresearch.com`. It is **NOT** testable through `sml-gateway` (a separate local provider at `localhost:3334`). These are completely separate authentication domains and infrastructure.

- sml-gateway at localhost:3334 has its own model mappings and provider routing
- nous at inference-api.nousresearch.com has its own OAuth system
- Testing nous through sml-gateway will fail (different model names, different auth, different infrastructure) even if both providers are healthy
- Each provider must be tested against its OWN endpoint URL

## What This Does NOT Verify

- OAuth token validity (requires actual credentials)
- Account balance / subscription status
- Whether the user's specific Hermes session can complete requests
- Rate limiting behavior

It verifies **server availability and API responsiveness** — which is often the most common point of failure. When providers go down, the first thing to check is whether the endpoint is reachable at all.