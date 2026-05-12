# Pitfalls & Known Issues

## Critical: Config Overwrite Bug (FIXED)

**Symptom**: Sentinel runs once, produces a correct report. The second run finds zero candidates, zero endpoints, and assigns nothing. Every subsequent run produces empty output.

**Root cause**: sentinel.py read `crispcraft-provider.conf`, ran health checks, then wrote the health results **back to the same file**, overwriting candidates, provider_endpoints, and job_registry. The second run loaded the health-output JSON (which has no candidates) and cascaded to empty.

**Fix**: Separate source config (read-only) from output file (overwritten):
- `sentinel-config.json` → source of truth, NEVER modified by sentinel.py
- `sentinel-output.json` → health results, overwritten each run

**Verification**: After any sentinel.py run, diff the source config to confirm it's unchanged.

---

## Cron Subagent: 400 Bad Request

**Symptom**: The Sentinel cron job reports `RuntimeError: 400 Bad Request` every run, while sentinel.py works perfectly when run manually.

**Root cause**: The Hermes cron subagent fails to initialize on certain providers (likely sml-gateway or the provider currently configured for the cron job). This is an infrastructure issue, not a sentinel code problem. Other cron jobs (Luna, Viktor, Kenji, etc.) show the same pattern.

**Workaround**: 
1. Assign the cron job a known-working model: `cronjob(action='update', job_id='<sentinel_id>', model={"provider":"ModelArk","model":"gpt-oss-120b-250805"})`
2. Wait for the Hermes Agent infrastructure fix

---

## API Discovery: Empty Response from /models

**Symptom**: Sentinel logs "Failed to discover provider: Expecting value: line 1 column 1 (char 0)" when auto-discovering custom_providers.

**Root cause**: Some providers (e.g., sml-gateway at `http://localhost:3334`) return non-JSON or empty responses on `/v1/models`. The JSON decode fails.

**Fix**: Wrap the discovery fetch in a try/except. Sentinel already does this — the error is caught and logged, and sentinel continues with configured candidates.

**Impact**: None. Auto-discovery is optional. Configured candidates are always tested.

---

## Gateway-Managed Providers Skip Direct Testing

**Behavior**: Providers with `key_source: "gateway"` are skipped in Step 1. Their health is NOT directly tested.

**Rationale**: Gateway-managed providers (e.g., nous on Hermes Agent's built-in gateway) have their keys managed internally. Direct curl testing would fail because sentinel doesn't have the key.

**Impact**: If a gateway-managed provider goes down, sentinel won't detect it directly. It relies on:
1. Auto-discovery (tests custom_providers from config.yaml if they have a base_url)
2. Cron job failures (broken jobs that use the provider will error, and the user will notice)

**Improvement**: Future versions could test gateway providers by calling the Hermes Agent API to check if they're responding.

---

## Cost Assignment: Paid-Per-Use Models

**Behavior**: If a provider is marked `"cost": "paid-per-use"`, sentinel will only assign it when no free or subscription alternative exists for the job.

**Risk**: If all free/subscription providers are down, sentinel WILL assign paid-per-use models to keep jobs running. This is intentional — better to pay per use than to have jobs completely fail.

**Mitigation**: Monitor the health report. If paid-per-use models are active, investigate why free/subscription providers are down.

---

## Job Type Mismatch

**Symptom**: A job shows "NO available model" even though working providers exist.

**Root cause**: The job's `type` in `job_registry` doesn't match any `job_types` in the available candidates. For example, if a job has type `"custom"` but all candidates have `job_types: ["all"]` or specific types that don't include "custom".

**Fix**: Ensure job types in `job_registry` match one of: `research`, `builder`, `content`, `marketing`, `design`, `finance`, `system`, `growth`. Or set `job_types: ["all"]` on candidates that can handle any job type.

---

## Latency Measurement Inaccuracy

**Issue**: sentinel.py measures latency via `time.monotonic()` around the curl subprocess call. This includes:
- Subprocess spawn time
- DNS resolution
- Network round-trip
- Response parsing
- Subprocess cleanup

This is **end-to-end latency**, not pure API response time. It's useful for relative comparison but may vary by 10-50ms between runs for the same provider.

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `SENTINEL_CONFIG` | `~/.hermes/sentinel-config.json` | Override source config path |

Only `SENTINEL_CONFIG` is used. No other env vars affect sentinel behavior. API keys are always read from `~/.hermes/config.yaml` (custom_providers section), not from env vars.
