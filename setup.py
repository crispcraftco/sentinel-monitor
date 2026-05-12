#!/usr/bin/env python3
"""
sentinel-monitor — Interactive Configuration Wizard

Guides the user through setting up providers, candidates, endpoints,
quality tiers, job registry, and cron jobs. Can pull existing cron
jobs from the Hermes Agent CLI automatically.

Usage: python3 ~/.hermes/skills/sentinel-monitor/setup.py
"""

import json
import os
import sys
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
HERMES_DIR = os.path.join(HOME, ".hermes")
CONFIG_PATH = os.path.join(HERMES_DIR, "sentinel-config.json")

os.makedirs(HERMES_DIR, exist_ok=True)


def load_existing_jobs():
    """Pull cron jobs from Hermes Agent CLI."""
    try:
        import subprocess
        r = subprocess.run(["hermes", "cron", "list"],
                           capture_output=True, text=True, timeout=20)
        if r.returncode == 0:
            data = json.loads(r.stdout)
            jobs = {}
            for j in data.get("jobs", []):
                jobs[j["job_id"]] = {
                    "name": j.get("name", ""),
                    "type": j.get("type", "")
                }
            return jobs
    except Exception:
        pass
    return {}


def prompt(text, default_val=""):
    """Show prompt, return user input (or default)."""
    if default_val:
        raw = input(f"  {text} [{default_val}]: ").strip()
        return raw if raw else default_val
    else:
        return input(f"  {text}: ").strip()


def collect_providers():
    """Interactively collect provider config. Returns (candidates, endpoints)."""
    print("\n━━━ LLM Providers ━━━")
    print("For each provider, enter:")
    print("  name  |  model id  |  cost (free/paid-subscription/paid-per-use)  |  tier (tier-1/tier-2/tier-3)  |  API URL  |  key source (config/none/gateway)")
    print("  You can also describe in natural language — e.g. 'I have ModelArk with gpt-oss-120b, paid-sub, tier-1'.")
    print("  Or enter piped format above. Leave blank when done.\n")

    candidates = []
    endpoints = {}

    while True:
        line = input("> ").strip()
        if not line:
            break
        # Try pipe-separated format
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 6:
            provider = parts[0]
            model = parts[1]
            cost_raw = parts[2].lower()
            tier = parts[3].lower()
            url = parts[4]
            key_src = parts[5].lower()

            cost = {"free": "free", "paid-sub": "paid-subscription",
                     "paid-subscription": "paid-subscription",
                     "paid-per-use": "paid-per-use", "paid": "paid-per-use"}.get(cost_raw, "paid-per-use")
            if not tier.startswith("tier-"):
                tier = f"tier-{tier}"

            candidates.append({
                "provider": provider, "model": model,
                "cost": cost, "quality": tier,
                "job_types": ["all"]
            })
            endpoints[provider] = {"url": url, "key_source": key_src}
            print(f"   ✓ added {provider}/{model} ({cost} {tier})")
        else:
            # Natural language - ask follow-up questions
            print("   I'll help you set this up. A few questions:")
            provider = prompt("Provider name (e.g. ModelArk, openrouter)", line.split()[0] if line else "")
            model = prompt("Model ID (e.g. claude-sonnet-4, gpt-4)")
            cost = prompt("Cost type [free/paid-subscription/paid-per-use]", "free")
            tier = prompt("Quality tier [tier-1/tier-2/tier-3]", "tier-2")
            url = prompt("API URL (endpoint for chat completions)")
            key_src = prompt("Key source [config/none/gateway]", "config")

            candidates.append({
                "provider": provider, "model": model,
                "cost": cost, "quality": tier,
                "job_types": ["all"]
            })
            endpoints[provider] = {"url": url, "key_source": key_src}
            print(f"   ✓ added {provider}/{model}")
            print()

    return candidates, endpoints


def main():
    print("╔══════════════════════════════════════════════════╗")
    print("║     Sentinel — Provider Health Monitor Setup     ║")
    print("╚══════════════════════════════════════════════════╝\n")

    # 1. Providers
    candidates, endpoints = collect_providers()
    if not candidates:
        print("No providers entered. Write config manually at:")
        print(f"  {CONFIG_PATH}")
        return

    # 2. Job registry
    print("\n━━━ Cron Jobs ━━━")
    print("Auto-discover existing cron jobs from Hermes Agent?")
    choice = prompt("[y/N]", "n").lower()
    job_registry = {}
    if choice == "y":
        job_registry = load_existing_jobs()
        if job_registry:
            print(f"  ✓ Found {len(job_registry)} cron jobs")
        else:
            print("  ⚠ No jobs found via CLI")
    else:
        print("Enter jobs manually. Leave job_id blank when done.\n")
        while True:
            jid = prompt("Job ID")
            if not jid:
                break
            jname = prompt("Job name")
            jtype = prompt("Job type (research/builder/content/marketing/design/finance/system/growth)")
            job_registry[jid] = {"name": jname, "type": jtype}

    # 3. Quality requirements
    print("\n━━━ Quality Requirements ━━━")
    defaults = {
        "research": "tier-1", "builder": "tier-1", "content": "tier-1",
        "marketing": "tier-2", "design": "tier-2", "finance": "tier-2",
        "system": "tier-3", "growth": "tier-3"
    }
    accept = prompt("Use default quality tiers? [Y/n]", "y").lower()
    if accept == "n":
        for jt, dv in defaults.items():
            defaults[jt] = prompt(f"{jt}", dv)

    # 4. Check interval
    interval = prompt("Health check interval", "1 hour")

    # 5. Assemble config
    config = {
        "candidates": candidates,
        "provider_endpoints": endpoints,
        "job_registry": job_registry,
        "quality_requirements": defaults,
        "cost_priority": ["free", "paid-subscription", "paid-per-use"],
        "check_interval": interval,
        "configured_at": datetime.now(timezone.utc).isoformat()
    }

    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    os.chmod(CONFIG_PATH, 0o600)
    print(f"\n✅ Config saved to {CONFIG_PATH}")
    print("   Test with: python3 ~/.hermes/skills/sentinel-monitor/scripts/sentinel.py")

    # 6. Cron job creation helper
    print("\n━━━ Create Sentinel Cron Job ━━━")
    print("Ask your Hermes Agent to create a cron job with this setup:\n")
    print("  cronjob(")
    print("    action='create',")
    print("    name='Sentinel Monitor',")
    print("    prompt='You are Sentinel — LLM Provider Health Monitor. Run:")
    print(f"      python3 {HOME}/.hermes/skills/sentinel-monitor/scripts/sentinel.py")
    print("      Read the output — it contains the health report and cron_updates.")
    print("      For each update: cronjob(action=update, job_id=..., model={{provider:..., model:...}})")
    print("      Deliver the report to the user.',")
    print("    schedule='0 * * * *',")
    print("    enabled_toolsets=['terminal', 'file', 'cronjob'],")
    print("  )")


if __name__ == "__main__":
    main()
