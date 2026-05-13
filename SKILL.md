---
name: sentinel-monitor
description: Generic LLM provider health monitor. Tests all API providers via actual chat completions, auto-assigns best available model per job based on quality tier and cost priority. Reusable across projects.
category: devops
---

# Sentinel — LLM Provider Health Monitor

## What It Does

Tests all configured API providers hourly by sending real chat completion requests. Assigns the best available model to each cron job based on:
1. Quality tier match (candidate quality >= job requirement)
2. Cost priority (free > paid-subscription > paid-per-use)
3. Latency (fastest wins)

Delivers health report and auto-updates cron jobs on provider failures.

## When to Load This Skill

- Setting up provider monitoring for any multi-provider deployment
- Diagnosing cascading agent failures due to API issues
- Configuring auto-failover for scheduled jobs
- Sharing a health monitor across multiple projects

## Setup

### Option A: Interactive setup
```bash
python3 ~/.hermes/skills/sentinel-monitor/setup.py
```
Guides through provider entry, endpoint config, cron job discovery, and quality tier setup.

### Option B: Manual config
Write `~/.hermes/sentinel-config.json` (see README.md for schema).

### Option C: Copy from template
```bash
cp ~/.hermes/skills/sentinel-monitor/templates/sentinel-config.example.json ~/.hermes/sentinel-config.json
# Edit with your real providers
```

### Mandatory: End-to-End Verification Before Claiming Complete

Do NOT say "it's working" until you've verified the complete pipeline:

1. **Uninstall first**: `bash ~/.hermes/skills/sentinel-monitor/uninstall.sh` — remove cron job + old config
2. **Run setup**: Create fresh config via `setup.py` or manual config write
3. **Test script directly**: `python3 ~/.hermes/skills/sentinel-monitor/scripts/sentinel.py` — verify it runs, tests providers, writes sentinel-output.json
4. **Verify output**: `cat ~/.hermes/sentinel-output.json` — confirm available/failed/assignments/cron_updates are populated
5. **Verify source config unchanged**: Confirm sentinel-config.json still has all candidates/endpoints (not wiped)
6. **Create and run cron job**: `cronjob(action='create')` then `cronjob(action='run')` — wait for output in cron log directory or delivery

Only after ALL steps pass can you report success. Direct script success is necessary but not sufficient.

## How Provider Testing Works

Always test via **actual chat completion requests**, not `/health` endpoints.
- `max_tokens=2` + `content="ping"` costs ~5 tokens
- Tests: server alive + auth valid + account has balance + model accessible
- HTTP 200 = ok, 429 = rate limited, 402 = no funds, 401/403 = auth failed, 5xx = server error

## Key Architecture

```
sentinel-config.json          ← source of truth (read-only)
      ↕
sentinel.py                   ← tests providers, assigns models
      ↕
sentinel-output.json          ← health check results (separate, overwritten)
      ↕
cronjob(action=update)        ← auto-switch jobs to working models
```

**Critical design decision**: Source config is never overwritten. Health output goes to a separate file (`sentinel-output.json`).

## Critical Pitfalls

### Cascading Config Wipe (CRITICAL — killed entire system in 1 run)

The script MUST never write results back to the source config. If `sentinel-config.json` is overwritten with output results, the next run finds zero candidates → tests nothing → writes zero data. Total system collapse in a single cycle.

**Pattern**: SOURCE (read-only) → process → OUTPUT (overwritten each run). Always separate files.

### Sentinel Cron Job `deliver` Target

Cron jobs with `deliver: "origin"` may fail with `platform 'discord' not configured/enabled` — this happens when a job was created from a conversation in another origin (e.g. web TUI) and `"origin"` doesn't resolve in Discord. Fix by updating the delivery target explicitly:
```
cronjob(action='update', job_id='<id>', deliver='discord:<channel_id>')
```
The current Discord server/channel ID is `1497816660185190531`.

### Gateway-Managed Providers (FIXED — were silently dropped from reports)

Candidates with `"key_source": "gateway"` (e.g. `nous/qwen3.6-plus` routed through `sml-gateway`) **used to be silently skipped** by `sentinel.py` line 177: `if ks=="gateway" or not url: continue` — which dropped them from the health report entirely, so the user would never see them.

**Fix applied in sentinel.py**: Gateway providers are now included in the `ok` list with `status: "gateway"` and `latency_ms: 0`, so they appear in the report as "gateway-managed (assumed operational)" instead of vanishing. The patched logic:
```python
if ks == "gateway":
    print(f"  ⚙ {p}/{m} -- gateway-managed (assumed operational)")
    ok.append({"provider":p,"model":m,"cost":cost,"quality":qual,
               "job_types":jtypes,"latency_ms":0,"status":"gateway",
               "tested_at":datetime.now(timezone.utc).isoformat()})
    continue
```
When auditing a sentinel installation, check `sentinel-config.json` provider_endpoints — any provider with `key_source: "gateway"` must be handled by the script (included in report), not skipped.

See `references/pitfalls.md` for detailed analysis of provider testing issues.
See `references/cascading-config-bug.md` for the source-vs-output config wipe pattern and fix.
See `references/crispcraft-job-registry.md` for the complete CrispCraft.co cron job registry.

## Cron Job Subagent Failures

Cron jobs frequently report `RuntimeError: 400 Bad Request` on the cron output log — this is a **Hermes subagent infrastructure issue**, NOT a problem with the sentinel script or provider configuration. The `sentinel.py` script runs perfectly when executed directly. The 400 error happens when the cron subagent tries to initialize. If the script works standalone but the cron job fails, the issue is in Hermes cron infrastructure.

## Uninstall
```bash
bash ~/.hermes/skills/sentinel-monitor/uninstall.sh
```

## Cron Job Creation — Prompt Pattern

The sentinel cron job's prompt MUST be detailed enough for the subagent to parse sentinel.py output and act on it. Minimal or vague prompts result in empty "(empty)" responses in the cron delivery log.

**Working prompt template**:
```
You are Sentinel — CrispCraft.co LLM Provider Health Monitor.

## Steps
1. Run this command and capture ALL output:
   python3 ~/.hermes/skills/sentinel-monitor/scripts/sentinel.py
2. Read the file `~/.hermes/sentinel-output.json` — it has health data, assignments, and cron_updates.
3. If the JSON has a cron_updates array with items, for each one call:
   cronjob(action='update', job_id='{job_id}', model={'provider': '{provider}', 'model': '{model}'})
4. Report results to the user:
   - Which providers are up/down
   - Which jobs got assigned to which models
   - Any cron updates that were applied
   - Latency numbers from the health check
```

**Key requirements**: Numbered steps, explicit command syntax, explicit report structure. Without this, subagents deliver "(empty)" even though the script ran correctly.

## setup.py Limitations

`setup.py` auto-discovery of cron jobs via `hermes cron list` often fails inside the piped input flow with "No jobs found via CLI". This is a known limitation — the hermes CLI may not be accessible during interactive setup.

**Critical: Always populate the complete job registry.** After setup.py creates config, you MUST add ALL cron jobs to the `job_registry`. Never leave it empty or partial — if the workspace has known cron jobs (e.g., CrispCraft agents: Space, Marcus, Luna, Viktor, Sofia, Olivia, Jax, Kenji, plus system jobs), add them ALL before running sentinel.py. Partial job registries mean sentinel won't monitor unlisted jobs during outages.

**How to discover**: Run `cronjob(action='list')` first to get all job IDs + names, then write the complete registry into sentinel-config.json.

