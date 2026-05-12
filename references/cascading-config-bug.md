# Cascading Config Wipe Bug — Pattern & Fix

## The Problem

Health monitor scripts that read a config file, process data, and write results back to the *same file* silently destroy their own input on the second run.

### How It Happens

```
Run 1: Read config.json → test providers → write results to config.json
Run 2: Read config.json (now contains results, not candidates) → zero candidates found → fail
Run 3: Same as Run 2 → permanent failure
```

The script overwrites candidates, provider_endpoints, and job_registry with output fields like available, failed, assignments — destroying the data it needs to function.

### Sentinel Case Study (May 2026)

The original sentinel-monitor read `crispcraft-provider.conf` for candidates, tested them, wrote results back to the same file, and on the next run found zero candidates — cascading into total provider detection failure across all cron jobs.

## The Fix: Separate Source from Output

```
sentinel-config.json   ← source of truth (read-only, NEVER overwritten)
sentinel-output.json   ← health check results (overwritten each run)
```

### Implementation Pattern

```python
# GOOD: separate paths
SOURCE_CONFIG = "~/.hermes/sentinel-config.json"   # input, never written
OUTPUT_CONFIG = "~/.hermes/sentinel-output.json"   # output, safe to overwrite

config = load_json(SOURCE_CONFIG)
# ... process ...
save_json(OUTPUT_CONFIG, results)  # different file!

# BAD: same path
CONFIG = "~/.hermes/sentinel-config.json"
config = load_json(CONFIG)
# ... process ...
save_json(CONFIG, results)  # destroys input!
```

### Checklist for Any Health Monitor Script

- [ ] Config path for reading is DIFFERENT from config path for writing
- [ ] Source config is loaded once and never modified
- [ ] Output is written to a separate file
- [ ] If source config is missing, return defaults (don't crash or create empty output)
- [ ] Uninstall script optionally removes output but keeps source config

### Additional Safeguard

If a script MUST write to the same file for some reason, it should:
1. Read the full config first
2. Preserve all input fields in the output
3. Use `config.update(results, keep=True)` rather than `config = results`

## Related Pitfalls

- Cron jobs that run hourly will hit this silently — failure shows up hours later
- The first run succeeds, making the bug hard to detect during initial setup
- Output files may look "correct" (valid JSON with expected keys) but contain wrong data
