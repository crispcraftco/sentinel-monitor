# Sentinel Pitfalls & Lessons Learned

## Cascading Config Wipe (CRITICAL)

**Bug:** Script wrote health check results back to the same config file it reads from. Run 1: config has candidates/endpoints → test → overwrite config with results. Run 2: config has no candidates → test nothing → write empty data. Total system collapse in 1 cycle.

**Fix:** Source config → read only. Output file → written each run. Never the same file.
```
sentinel-config.json   ← source (NEVER overwritten)
sentinel-output.json   ← results (overwritten each run)
```

## Cron Job Self-Hosting

The sentinel cron job itself runs on a model provider. If that provider goes down, the cron never fires and cannot update other jobs. Always assign the sentinel cron to a reliable provider (a free or local gateway that is unlikely to fail).

## Gateway-Skipped Candidates

Providers with `key_source: gateway` have no direct URL to curl — sentinel skips them. To detect gateway provider failures, use the sentinel's own execution as a heartbeat: if sentinel runs, at least one gateway provider is alive.
