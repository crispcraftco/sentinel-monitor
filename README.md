# Sentinel — LLM Provider Health Monitor

Automated health monitoring for multi-provider LLM deployments. Tests all configured providers via **actual chat completion requests**, auto-assigns the best available model to each job based on quality tier and cost priority, and reports health status.

Generic and reusable — works for any project using multiple LLM providers.

## Quick Start

### 1. Install

```bash
git clone https://github.com/crispcraftco/sentinel-monitor.git ~/.hermes/skills/sentinel-monitor
```

### 2. Configure

Run the interactive setup wizard:
```bash
bash ~/.hermes/skills/sentinel-monitor/install.sh
# or: python3 ~/.hermes/skills/sentinel-monitor/setup.py
```

You'll be guided through:
- **LLM Providers** — Name, model ID, cost type, quality tier, API URL, key source
- **Cron Jobs** — Auto-discover from Hermes Agent or enter manually
- **Quality Tiers** — Per job type requirements (default: research/builder/content = tier-1, marketing/design/finance = tier-2, system/growth = tier-3)
- **Health Check Interval** — How often to run (default: every 1 hour)

### 3. Test
```bash
python3 ~/.hermes/skills/sentinel-monitor/scripts/sentinel.py
```

### 4. Create the Cron Job
Ask your Hermes Agent to create a Sentinel cron job:
```python
cronjob(
    action='create',
    name='Sentinel Monitor',
    prompt='Run: python3 ~/.hermes/skills/sentinel-monitor/scripts/sentinel.py. Read the output and sentinel-output.json. For each update in cron_updates: cronjob(action=update, job_id=..., model=...).',
    schedule='0 * * * *',
    enabled_toolsets=['terminal', 'file', 'cronjob'],
)
```

## How It Works

1. **Reads config** from `~/.hermes/sentinel-config.json` (or `SENTINEL_CONFIG` env var)
2. **Tests each provider** via actual chat completion with `max_tokens=2` — real auth, real endpoint, real model
3. **Builds available list** of working providers (HTTP 200 responses)
4. **Assigns best model** per job using: quality tier match → cheapest cost → fastest latency
5. **Writes output** to `~/.hermes/sentinel-output.json` (never overwrites the source config!)
6. **Prints health report** for the calling agent to deliver

## Configuration File

Location: `~/.hermes/sentinel-config.json`

```json
{
  "candidates": [
    {
      "provider": "openai",
      "model": "gpt-4o",
      "cost": "paid-subscription",
      "quality": "tier-1",
      "job_types": ["all"]
    },
    {
      "provider": "my-gateway",
      "model": "fast-model",
      "cost": "free",
      "quality": "tier-2",
      "job_types": ["system", "growth"]
    }
  ],
  "provider_endpoints": {
    "openai": {
      "url": "https://api.openai.com/v1/chat/completions",
      "key_source": "config"
    },
    "my-gateway": {
      "url": "http://localhost:8080/v1/chat/completions",
      "key_source": "none"
    }
  },
  "job_registry": {
    "job-abc123": {"name": "Daily Report", "type": "research"}
  },
  "quality_requirements": {
    "research": "tier-1",
    "builder": "tier-1",
    "content": "tier-1",
    "marketing": "tier-2",
    "design": "tier-2",
    "finance": "tier-2",
    "system": "tier-3",
    "growth": "tier-3"
  },
  "cost_priority": ["free", "paid-subscription", "paid-per-use"]
}
```

### Fields

| Field | Description |
|---|---|
| `candidates` | List of provider/model pairs to test. Each has cost type, quality tier, and supported job types. |
| `provider_endpoints` | API URL and key source per provider. Key source: `config` (read from ~/.hermes/config.yaml), `none` (no key), `gateway` (managed by Hermes). |
| `job_registry` | Map of job IDs to name + type. Auto-discovered by setup.py via `hermes cron list`. |
| `quality_requirements` | Minimum quality tier per job type. Default: research/builder/content = tier-1, marketing/design/finance = tier-2, system/growth = tier-3. |
| `cost_priority` | Priority order for cost types. Default: free > paid-subscription > paid-per-use. |

### Key Source Types

- **config** — API key is in `~/.hermes/config.yaml` under `custom_providers`
- **none** — No API key needed (e.g., local gateways with no auth)
- **gateway** — Key managed by Hermes Agent gateway, skip from direct curl test

### Cost Types

- **free** — No incremental cost (free APIs, local models)
- **paid-subscription** — You pay a flat monthly fee. Marginal cost is zero — use freely
- **paid-per-use** — Pay per token. Use as fallback only.

### Quality Tiers

| Tier | Purpose | Example |
|---|---|---|
| tier-1 | Strong reasoning, long context, tool calling | Claude Sonnet, GPT-4, Qwen 3 |
| tier-2 | Good reasoning, tool calling, 32K+ context | GPT-4o-mini, Mistral Large |
| tier-3 | Fast response, basic reasoning | Small local models, fast gateways |

## Health Report Output

### sentinel-output.json (machine-readable)
```json
{
  "available": [{"provider": "...", "model": "...", "cost": "...", "latency_ms": 242}],
  "failed": [{"provider": "...", "model": "...", "status": "server_error", "http_code": 500}],
  "assignments": {"job-id": {"provider": "...", "model": "...", "cost": "..."}},
  "cron_updates": [{"job_id": "...", "provider": "...", "model": "..."}]
}
```

### stdout (human-readable)
Printed during script execution and delivered by the cron job.

## Uninstall

```bash
bash ~/.hermes/skills/sentinel-monitor/uninstall.sh
```

Removes the cron job and optionally the config. The skill directory is kept for reinstallation.

## Project Structure

```
sentinel-monitor/
├── scripts/
│   └── sentinel.py          # Health monitor — the worker
├── setup.py                  # Interactive configuration wizard
├── install.sh                # Wrapper that calls setup.py
├── uninstall.sh              # Clean removal script
├── SKILL.md                  # Skill metadata for Hermes Agent
├── templates/
│   └── sentinel-config.example.json  # Example config
└── README.md                 # This file
```

## API Key Security

API keys are never stored in sentinel-config.json. They are read from your Hermes Agent config (`~/.hermes/config.yaml`, `custom_providers` section) or your operating system keychain. The test request uses `max_tokens=2` which costs ~5 tokens per test.

## License

MIT