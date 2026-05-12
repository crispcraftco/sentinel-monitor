# CrispCraft.co Cron Job Registry (Production)

Current set of 14 cron jobs. Sentinel must monitor ALL of these — partial
registries leave jobs unprotected during provider outages.

| Job ID | Name | Type | Quality Req |
|---|---|---|---|
| 0303e23b77ff | Daily Vault Ingest + LINT | builder | tier-1 |
| 769d44c912f2 | Space Daily Report | research | tier-1 |
| bf461e22c666 | Space 6-Hour Log | system | tier-3 |
| 1b428cd5e75c | Space Wake Check | system | tier-3 |
| fe872add1bd5 | Daily Business Report | marketing | tier-2 |
| e05cee687b9b | Monthly Business Report | research | tier-1 |
| 30652ac5f57e | Marcus — Product Quality Review | research | tier-1 |
| 1bcd769acd9c | Luna — Design & Mockup Agent | design | tier-2 |
| 2b11fc3e1872 | Viktor — Marketing & Publishing Agent | marketing | tier-2 |
| 1c2307b729ff | Sofia — Product Builder Agent | builder | tier-1 |
| 260ae0d82e34 | Olivia — Content Writer Agent | content | tier-1 |
| 55db52a044f7 | Jax — Growth & Community Agent | growth | tier-3 |
| 59296cfbfc4a | Kenji — Finance & Compliance Agent | finance | tier-2 |
| 3e2227e8f924 | Sentinel Monitor | system | tier-3 |

All agents run on `sml-gateway` (free, local gateway at localhost:3334/v1)
as the primary provider. Sentinel is assigned to `sml/auto` (free, fastest).

**Provider tiers**:
- Free: sml-gateway (sml/tools, sml/auto)
- Paid-subscription: ModelArk (gpt-oss-120b, seed-2-0-pro, seed-2-0-lite, seed-2-0-mini)
- Gateway-managed (not directly testable): nous (qwen3.6-plus)

Workspace: ~/Projects/crispcraftco/
