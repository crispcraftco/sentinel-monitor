#!/usr/bin/env python3
"""
Sentinel -- LLM Provider Health Monitor

Tests all API providers via real chat completion requests.
Builds optimal job -> model assignments based on quality tier + cost priority.
Reads config (never overwritten), writes output to separate file.

Config:  $HOME/.hermes/sentinel-config.json  (or SENTINEL_CONFIG env var)
Output:  $HOME/.hermes/sentinel-output.json
"""

import json, os, subprocess, time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
SRC = os.environ.get("SENTINEL_CONFIG", os.path.join(HOME, ".hermes", "sentinel-config.json"))
OUT = os.path.join(HOME, ".hermes", "sentinel-output.json")

TIERS  = {"tier-1": 1, "tier-2": 2, "tier-3": 3}
COSTS  = {"free": 0, "paid-subscription": 1, "paid-per-use": 2}

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path=None):
    path = path or SRC
    defaults = {
        "candidates": [],
        "provider_endpoints": {},
        "job_registry": {},
        "quality_requirements": {
            "research":"tier-1","builder":"tier-1","content":"tier-1",
            "marketing":"tier-2","design":"tier-2","finance":"tier-2",
            "system":"tier-3","growth":"tier-3",
        },
        "cost_priority": ["free","paid-subscription","paid-per-use"],
        "check_interval": "1 hour",
    }
    try:
        with open(path) as f:
            cfg = json.load(f)
        for k,v in defaults.items():
            cfg.setdefault(k, v)
        return cfg
    except FileNotFoundError:
        return defaults

def load_hermes_config():
    """Read ~/.hermes/config.yaml for custom_provider API keys.
    Tries multiple locations for resilience across environments."""
    cfg_path = os.path.join(HOME, ".hermes", "config.yaml")
    try:
        import yaml
        with open(cfg_path) as f:
            return yaml.safe_load(f) or {}
    except (ImportError, FileNotFoundError, PermissionError):
        return {}


# Known env var names for provider API keys
KEY_ENV_VARS = {
    "ModelArk": ["MODELARK_API_KEY", "MODELPARK_API_KEY", "BYTEPLUS_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY", "OR_API_KEY"],
    "nous": ["NOUS_API_KEY", "NOUS_INFERENCE_KEY"],
}

# Known file paths for provider API keys
KEY_FILES = {
    "ModelArk": [os.path.join(HOME, ".hermes", ".modelark_key"),
                 os.path.join(HOME, ".hermes", ".modelark_api_key")],
    "openrouter": [os.path.join(HOME, ".hermes", ".openrouter_key")],
}


def get_api_key(name, ks, hc):
    """Get a provider API key from multiple sources.
    Falls back from hermes config -> env vars -> key files.
    Returns None if key is not found anywhere."""
    if ks != "config":
        return None

    # 1. Try hermes config.yaml custom_providers
    for cp in hc.get("custom_providers", []):
        if cp.get("name") == name:
            key = cp.get("api_key")
            if key:
                return key

    # 2. Try environment variables (known provider key env var names)
    env_names = []
    for n in [name, name.lower(), name.replace("-", "_").replace("_", "")]:
        env_names += [f"{n.upper()}_API_KEY", f"{n.upper()}_KEY"]
    # Also try specific known names
    env_names += ["MODELARK_API_KEY", "OPENROUTER_API_KEY", "NOUS_API_KEY"]
    for env_name in env_names:
        key = os.environ.get(env_name)
        if key:
            return key

    # 3. Try key files in ~/.hermes/
    dotfile_paths = [
        os.path.join(HOME, ".hermes", f".{name.lower()}_key"),
        os.path.join(HOME, ".hermes", f".{name.lower()}_api_key"),
        os.path.join(HOME, ".hermes", f".{name.lower()}.key"),
    ]
    for fpath in dotfile_paths:
        try:
            with open(fpath) as f:
                key = f.read().strip()
                if key:
                    return key
        except (FileNotFoundError, PermissionError):
            pass

    return None


def check_key_status(provider, ks, hc):
    """Check if a provider has a usable key. Returns (has_key, reason)."""
    if ks == "none":
        return True, "no key needed"
    if ks == "gateway":
        return True, "managed by gateway"

    key = get_api_key(provider, ks, hc)
    if key:
        return True, f"key found (length {len(key)})"
    return False, "no key available in config.yaml, env vars, or key files"

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

def test_one(provider, model, url, key=None, timeout=15):
    """Hit a provider/model with a tiny chat request. Return (status, ms, http)."""
    payload = json.dumps({"model":model, "messages":[{"role":"user","content":"ping"}], "max_tokens":2})
    cmd = ["curl","-s","-o","/dev/null","-w","%{http_code} %{time_total}",
           "-m",str(timeout), url,
           "-H","Content-Type: application/json"]
    if key:
        cmd += ["-H", f"Authorization: Bearer {key}"]
    cmd += ["-d", payload]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+5)
        ms = int((time.monotonic()-t0)*1000)
        parts = r.stdout.strip().split()
        code = int(parts[0]) if parts else 0
        if code == 200:   return ("ok", ms, code)
        if code == 429:   return ("rate_limited", ms, code)
        if code == 402:   return ("insufficient_funds", ms, code)
        if code == 404:   return ("model_not_found", ms, code)
        if code in (401,403): return ("auth_failed", ms, code)
        if code == 0:     return ("timeout", ms, code)
        return ("server_error", ms, code)
    except Exception:
        return ("timeout", int((time.monotonic()-t0)*1000), 0)

# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step1_test(candidates, endpoints, hc, cfg_path):
    """Test every candidate. Returns (ok_list, fail_list)."""
    print("━━━ Step 1: Testing candidates ━━━")
    ok, fail = [], []
    for c in candidates:
        p, m = c["provider"], c["model"]
        cost   = c.get("cost", "paid-per-use")
        qual   = c.get("quality", "tier-3")
        jtypes = c.get("job_types", ["all"])
        ep     = endpoints.get(p, {})
        url    = ep.get("url","")
        ks     = ep.get("key_source","gateway")
        if ks == "gateway":
            # Gateway-managed providers (e.g., OAuth-authenticated upstream like Nous).
            # Test via hermes CLI which handles OAuth tokens, rate limits, and model routing.
            cmd = ["hermes", "chat", "-q", "ping", "-m", m, "--provider", p, "-Q"]
            t0 = time.monotonic()
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                ms = int((time.monotonic()-t0)*1000)
                stdout = r.stdout.strip()
                # hermes exits 0 and prints response body when model works
                if r.returncode == 0 and stdout and "error" not in stdout.lower():
                    ok.append({"provider":p,"model":m,"cost":cost,"quality":qual,
                               "job_types":jtypes,"latency_ms":ms,"status":"cli_ok",
                               "tested_via":"hermes_cli","tested_at":datetime.now(timezone.utc).isoformat()})
                    print(f"  ✅ {p}/{m} -- {qual} via hermes CLI ({ms}ms)")
                else:
                    err = r.stderr.strip()[:300] or stdout[:300] or "empty output"
                    fail.append({"provider":p,"model":m,"status":"cli_fail","latency_ms":ms,
                                 "output":err})
                    print(f"  ❌ {p}/{m} -- cli_fail ({ms}ms): {err[:100]}")
            except subprocess.TimeoutExpired:
                ms = int((time.monotonic()-t0)*1000)
                fail.append({"provider":p,"model":m,"status":"timeout","latency_ms":ms})
                print(f"  ❌ {p}/{m} -- timeout ({ms}ms)")
            except Exception as e:
                ms = int((time.monotonic()-t0)*1000)
                fail.append({"provider":p,"model":m,"status":"cli_error","latency_ms":ms,
                             "error":str(e)})
                print(f"  ❌ {p}/{m} -- cli_error: {e}")
            continue
        if not url:
            print(f"  ⚙ {p}/{m} -- no-url (skipped)")
            continue

        # Check if key is available before spending time testing
        has_key, key_reason = check_key_status(p, ks, hc)
        if not has_key:
            print(f"  ⚠ {p}/{m} -- SKIP: {key_reason}")
            fail.append({"provider":p,"model":m,"status":"missing_key","http_code":0})
            continue

        key = get_api_key(p, ks, hc)
        st, ms, code = test_one(p, m, url, key)
        if st == "ok":
            ok.append({"provider":p,"model":m,"cost":cost,"quality":qual,
                       "job_types":jtypes,"latency_ms":ms,"status":"ok",
                       "tested_at":datetime.now(timezone.utc).isoformat()})
            ci = {"free":"💚","paid-subscription":"💳","paid-per-use":"💲"}.get(cost,"💲")
            print(f"  ✅ {ci} {p}/{m} -- {qual} ({ms}ms)")
        else:
            fail.append({"provider":p,"model":m,"status":st,"http_code":code})
            print(f"  ❌ {p}/{m} -- {st} (HTTP {code})")
    return ok, fail

def assign_best(ok_list, job_registry, qreqs):
    """For every job, pick: quality >= requirement, then cheapest, then fastest."""
    print("\n━━━ Step 2: Best-model assignment ━━━")
    out = {}
    for jid, ji in job_registry.items():
        name, jtyp = ji.get("name",jid), ji.get("type","")
        req = qreqs.get(jtyp, "tier-3")
        pool = [c for c in ok_list if c.get("status") in ("ok", "gateway_ok", "cli_ok")
                and ("all" in c.get("job_types",["all"]) or jtyp in c.get("job_types",[]))]
        def rank(c):
            q_ok = TIERS.get(c.get("quality","tier-3"),99) <= TIERS.get(req,99)
            return (not q_ok, COSTS.get(c.get("cost","paid-per-use"),99), c.get("latency_ms", 9999))
        pool.sort(key=rank)
        if not pool:
            print(f"  ❌ {name} ({jtyp}/{req}) -- NO available model")
            out[jid] = None
            continue
        b = pool[0]
        q_ok = TIERS.get(b.get("quality","tier-3"),99) <= TIERS.get(req,99)
        tag = "✅" if q_ok else "⚠"
        print(f"  {tag} {name} -> {b['provider']}/{b['model']} ({b.get('cost')} {b.get('quality')})")
        out[jid] = {"provider":b["provider"],"model":b["model"],
                     "cost":b.get("cost"),"quality":b.get("quality")}
    return out

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    now = datetime.now(timezone.utc).isoformat()
    print(f"🛡 Sentinel -- {now}")
    print("="*60)
    cfg = load_config()
    if not cfg["candidates"]:
        # Also try the hermes config.yaml for custom providers and build candidates
        hc = load_hermes_config()
        cps = hc.get("custom_providers", [])
        if not cps:
            print("No candidates in config and no custom_providers in config.yaml.")
            print("Run: python3 ~/.hermes/skills/sentinel-monitor/setup.py")
            return

    hc = load_hermes_config()

    # Test
    ok, fail = step1_test(cfg["candidates"], cfg["provider_endpoints"], hc, SRC)

    # Also test custom_providers from config.yaml that aren't in sentinel config
    tested_providers = {c["provider"] for c in cfg["candidates"]}
    for cp in hc.get("custom_providers", []):
        name = cp.get("name","")
        if name in tested_providers:
            continue
        url = cp.get("base_url","")
        key = cp.get("api_key")
        if not url:
            continue
        # Fetch /models
        try:
            r2 = subprocess.run(["curl","-s","-m","6", url.rsplit("/",1)[0]+"/models"],
                                capture_output=True, text=True, timeout=10)
            models = json.loads(r2.stdout).get("data", [])
            for mi in models[:3]:
                mid = mi["id"]
                st, ms, code = test_one(name, mid, url, key)
                if st == "ok":
                    ok.append({"provider":name,"model":mid,"cost":"free","quality":"tier-2",
                               "job_types":["all"],"latency_ms":ms,"status":"ok"})
                    print(f"  🔍 {name}/{mid} -- ok ({ms}ms) [discovered]")
                else:
                    print(f"  🔍 {name}/{mid} -- {st}")
        except Exception as e:
            print(f"  ⚠ Failed to discover {name}: {e}")

    # Assign
    asgn = assign_best(ok, cfg.get("job_registry",{}), cfg.get("quality_requirements",{}))

    # Build output dict
    updates = [{"job_id":jid,"provider":a["provider"],"model":a["model"]}
               for jid,a in asgn.items() if a]
    out_dict = {
        "last_tested_at": now,
        "available": [{"provider":x["provider"],"model":x["model"],"cost":x.get("cost"),
                       "quality":x.get("quality"),"latency_ms":x.get("latency_ms")} for x in ok],
        "failed":     [{"provider":x["provider"],"model":x["model"],"status":x.get("status")} for x in fail],
        "assignments":{jid:a for jid,a in asgn.items() if a},
        "cron_updates": updates,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out_dict, f, indent=2)
    print(f"\n💾 {OUT}")

    # Human report
    print("\n━━━ Health Summary ━━━")
    free_ok = [x for x in ok if x.get("cost")=="free"]
    sub_ok  = [x for x in ok if x.get("cost")=="paid-subscription"]
    if free_ok:
        print("✅ FREE:")
        for x in free_ok: print(f"   {x['provider']}/{x['model']} ({x.get('quality')} {x['latency_ms']}ms)")
    if sub_ok:
        print("✅ SUBSCRIPTION:")
        for x in sub_ok: print(f"   {x['provider']}/{x['model']} ({x.get('quality')} {x['latency_ms']}ms)")
    if fail:
        print("❌ DOWN:")
        for x in fail: print(f"   {x['provider']}/{x['model']} -- {x['status']}")
    fn = sum(1 for v in asgn.values() if v and v.get("cost")=="free")
    sn = sum(1 for v in asgn.values() if v and v.get("cost")=="paid-subscription")
    print(f"\n📊 {len(ok)} up, {len(fail)} down")
    print(f"📊 {fn} jobs on free, {sn} on subscription")
    print(f"📊 {len(updates)} cron updates needed")
    if updates:
        jr = cfg.get("job_registry",{})
        for u in updates:
            print(f"   {jr.get(u['job_id'],{}).get('name',u['job_id'])} -> {u['provider']}/{u['model']}")

if __name__ == "__main__":
    main()
