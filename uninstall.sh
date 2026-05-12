#!/bin/bash
# sentinel-monitor — Uninstall Script
HERMES_DIR="$HOME/.hermes"
SKILL_DIR="$HERMES_DIR/skills/sentinel-monitor"
CONFIG="$HERMES_DIR/sentinel-config.json"
OUTPUT="$HERMES_DIR/sentinel-output.json"

echo "=== Uninstalling Sentinel Monitor ==="
echo ""

# 1. Remove cron job via hermes CLI
SENTINEL_ID=$(hermes cron list 2>&1 | grep -i "sentinel" | grep -oE '[0-9a-f]{12}' | head -1)
if [ -n "$SENTINEL_ID" ]; then
    hermes cron remove "$SENTINEL_ID" 2>/dev/null && echo "✓ Removed Sentinel cron job ($SENTINEL_ID)"
fi

# 2. Ask about config
read -p "Remove config file ($CONFIG)? [y/N]: " rmcfg
if echo "$rmcfg" | grep -qi "^y"; then
    rm -f "$CONFIG" && echo "✓ Removed config"
else
    echo "✓ Keeping config"
fi

# 3. Remove output file
rm -f "$OUTPUT" && echo "✓ Removed output file"

echo "✓ Done. Skill directory kept at $SKILL_DIR"
echo "  To fully remove: rm -rf $SKILL_DIR"
