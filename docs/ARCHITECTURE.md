# Architecture

## Overview

`github-accel` is a two-component system:

1. **`github-accel`** — Bash-based lifecycle manager (start/stop/status + watcher)
2. **`github-fastip.py`** — Python-based discovery engine (scan/verify/generate)

```
┌─────────────────────────────────────────────────────────┐
│                    User (CLI)                            │
│  ./github-accel start | stop | status                   │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  github-accel (Bash)                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ start       │  │ stop        │  │ status          │  │
│  │ → scan      │  │ → kill pid  │  │ → check hosts   │  │
│  │ → write     │  │ → clean     │  │ → check pid     │  │
│  │ → start     │  │   hosts     │  │ → edge windows  │  │
│  │   watcher   │  │             │  │ → latency test  │  │
│  └──────┬──────┘  └─────────────┘  └─────────────────┘  │
└─────────┼────────────────────────────────────────────────┘
          │
┌─────────▼────────────────────────────────────────────────┐
│  github-fastip.py (Python)                                │
│  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────────┐  │
│  │ Fetch    │→ │ TCP Scan │→ │ Verify │→ │ Generate │  │
│  │ meta API │  │ (100x    │  │ (curl  │  │ hosts    │  │
│  │          │  │  threads)│  │  +SSL) │  │  rules   │  │
│  └──────────┘  └──────────┘  └────────┘  └──────────┘  │
└──────────────────────────────────────────────────────────┘
```

## Component Details

### github-accel (Bash)

**File**: `github-accel` (top-level executable)

| Function | Description |
|----------|-------------|
| `clean_hosts()` | Removes [HOSTS_MARKER] block from `/etc/hosts` via `sed` |
| `install_hosts()` | Appends generated hosts rules (requires sudo) |
| `edge_window_count()` | Uses `osascript` to count Microsoft Edge windows |
| `start_watcher()` | Backgrounds a loop checking Edge windows every 3s |
| `stop_watcher()` | Kills watcher PID |
| `show_status()` | Reports hosts/watcher/Edge state + curl latency |

**State files**:
- `/tmp/github-accel-watch.pid` — Watcher process PID
- `/tmp/github_hosts_raw_only.txt` — Generated hosts rules (from scanner)

### github-fastip.py (Python)

**File**: `scripts/github-fastip.py`

| Stage | Method | Description |
|-------|--------|-------------|
| Fetch | `fetch_meta()` | GET `api.github.com/meta`, parse JSON CIDR ranges |
| Prepare | `prepare_targets()` | Filter IPs by [DOMAIN_IP_PREFERENCE] prefixes |
| Scan | `GithubFastScan.scan_domain()` | 100-thread pool, TCP 443, 1.5s timeout |
| Verify | `curl_verify()` | curl --connect-to → SSL handshake → TCP fallback |
| Generate | `get_hosts_lines()` | Output /etc/hosts format with markers |
| Install | `install_hosts()` | Replace marker block, write to /etc/hosts |

**Caching**: Results cached in `~/.cache/github-fastip/fastest_ips.json` (1h TTL).

## Data Flow

### Start Sequence

```
1. CLI: ./github-accel start
2. Bash: check /tmp/github_hosts_raw_only.txt exists
   - If not: python3 github-fastip.py --scan-only --output ...
3. Bash: check Edge is running (osascript)
4. Bash: sudo write hosts
5. Bash: start background watcher (3s loop)
6. Bash: print confirmation
```

### Watcher Loop

```
while true:
    sleep 3
    if edge_window_count() == 0:
        clean_hosts()
        delete pidfile
        exit
```

### Auto-Disconnect

```
Edge window count = 0 for 3+ seconds
    → bash watcher detects
    → calls clean_hosts() (removes marker block from /etc/hosts)
    → DNS reverts to default
    → watcher PID exits
```

## Key Design Decisions

### Why hosts injection instead of DNS hijack?

| Approach | Pros | Cons |
|----------|------|------|
| Hosts injection | Simple, no daemon, no port 53 | Needs sudo, per-machine |
| DNS hijack (FastGithub) | Multi-device sharing, dynamic | Requires .NET, root daemon |

For a personal developer machine, hosts injection is the pragmatic choice.

### Why three-tier verification?

1. **curl --connect-to**: Real HTTP transaction, catches 400/500 errors
2. **Python SSL handshake**: Works where curl's SNI handling fails (Fastly CDN)
3. **TCP fallback**: Last resort — if TCP 443 opens, hosts routing works

### Why skip github.com acceleration?

GitHub's own server segment (140.82.x.x) returned HTTP 400 on this network
due to strict CDN routing policies. The Azure-routed 322ms is acceptable.
The real bottleneck is `raw.githubusercontent.com` at 10s+ timeout, which
benefits from Fastly CDN acceleration to ~78ms.

## Edge Cases

- **Edge not running**: `start` exits with clear message, no hosts modification
- **No accessible IPs**: Script exits code 2, hosts untouched
- **Concurrent starts**: Old watcher PID killed before starting new one
- **Cache stale**: `--no-cache` or wait 1 hour
- **Permission denied**: Prints manual install instructions + hosts content