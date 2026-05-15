# ModelArk Authentication Guidance

The Sentinel monitor (and any cron job using the `modelark` provider) requires a valid API key to be exported as `MODELARK_API_KEY` in `~/.hermes/.env`.

## to add the key
1. Obtain the API token from your ModelArk account dashboard.
2. Open the Hermes environment file:
   ```bash
   nano ~/.hermes/.env
   ```
3. Add (or update) the line:
   ```bash
   export MODELARK_API_KEY="YOUR_MODELARK_TOKEN"
   ```
4. Save the file and reload the environment for the current session:
   ```bash
   source ~/.hermes/.env
   ```
5. Verify the key is set:
   ```bash
   echo $MODELARK_API_KEY
   ```
   It should print the token (or at least a non‑empty string).

## Why it matters
- Without the key, any request to `https://ark.ap-southeast.bytepluses.com/api/coding/v3` returns an `AuthenticationError` (HTTP 401) as seen in the Sofia job failure.
- Sentinel will mark the provider as `gateway_warn` or `auth_failed`, and the job will continue to use a mismatched endpoint.

## Common pitfalls
- Forgetting to export the variable (just adding `MODELARK_API_KEY=...` without `export`).
- Adding the key to a different `.env` file (e.g., project‑specific) instead of the global `~/.hermes/.env` used by Hermes.
- Using an expired or revoked token – generate a fresh one from the ModelArk console.

After setting the key, re‑run the Sofia job or the Sentinel health check to confirm the model `seed-2-0-lite-260228` is reachable.
