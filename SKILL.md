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

### Sentinel Cron Job Provider Assignment

The sentinel cron job itself runs on a model. If that provider goes down, the sentinel never fires and cannot update other jobs. Always assign sentinel to a reliable provider (free tier or local gateway).

### Gateway-Skipped Candidates

Providers with `key_source: "gateway"` cannot be tested via direct curl. Use sentinel's own execution as a heartbeat — if it ran, at least one gateway provider is alive.

See `references/pitfalls.md` for detailed analysis.

## Uninstall
```bash
bash ~/.hermes/skills/sentinel-monitor/uninstall.sh
```
