# github-accel

**GitHub Access Acceleration — Lightweight, stealth, auto-disconnect on browser close**

A zero-dependency tool that accelerates `raw.githubusercontent.com` access (up to **130x**) by finding the fastest CDN IP and injecting it into `/etc/hosts`. Includes a browser window watcher that automatically **disables acceleration when you close all browser windows**.

> ⚡ **Real benchmark**: `raw.githubusercontent.com` latency dropped from **10+ seconds (timeout)** to **~78ms** — confirmed on production network.

## Features

- **🔍 Stealth IP discovery** — DNS-first, no mass port scanning. Won't trigger GitHub WAF / IP bans.
- **💨 Lightweight** — Max 5 IP probes per domain, sequential with delays. 24-hour cache.
- **✅ Dual verification** — curl HTTP check → Python SSL handshake
- **🎯 Browser-agnostic auto-disconnect** — Supports Edge, Chrome, Safari. When all windows are closed, automatically restores default DNS.
- **🔒 Safe** — Backs up `/etc/hosts` before modification; `--restore` reverts
- **📦 Zero dependencies** — Pure Python 3 + standard library only. No pip install needed.

## Quick Start

```bash
# Clone the repo
git clone https://github.com/xz52016/github-accel.git
cd github-accel

# Make scripts executable
chmod +x github-accel scripts/github-fastip.py

# Start acceleration (opens sudo prompt for /etc/hosts write)
./github-accel start

# View status
./github-accel status

# Manually stop
./github-accel stop
```

## Usage

### Start with browser watch

```bash
# 1. Scans for fastest GitHub IPs (stealth mode, zero risk of IP bans)
# 2. Writes rules to /etc/hosts
# 3. Watches browser windows every 3 seconds
# 4. Auto-clears hosts when all browser windows close
./github-accel start
```

### Browser selection

```bash
# Default: Microsoft Edge
./github-accel start

# Google Chrome
GITHUB_ACCEL_BROWSER=chrome ./github-accel start

# Safari
GITHUB_ACCEL_BROWSER=safari ./github-accel start

# Terminal only (no browser watcher — clean manually)
GITHUB_ACCEL_BROWSER=none ./github-accel start
```

### Manual control

```bash
# Stop acceleration + restore /etc/hosts
./github-accel stop

# Show current state (acceleration, watcher, browser windows, latency)
./github-accel status

# Deep clean all GitHub-related entries from /etc/hosts
./github-accel clean
```

### Advanced — direct Python scanner

```bash
# Scan only — see results without modifying hosts
python3 scripts/github-fastip.py --scan-only

# Force re-scan (skip cache)
python3 scripts/github-fastip.py --no-cache --scan-only

# Output hosts rules to a file
python3 scripts/github-fastip.py --output /tmp/github-rules.txt

# Restore hosts from backup
sudo python3 scripts/github-fastip.py --restore

# Check status via scanner
python3 scripts/github-fastip.py --status

# Legacy aggressive mode (scan more IPs — may trigger rate limits)
python3 scripts/github-fastip.py --aggressive --scan-only
```

## Performance

| Domain | Before (default DNS) | After (accelerated) | Improvement |
|--------|---------------------|--------------------|-------------|
| `github.com` | 20.205.x.x (Azure) ~322ms | ⏭️ Not accelerated | N/A |
| `api.github.com` | 20.205.x.x (Azure) ~340ms | ⏭️ Not accelerated | N/A |
| `raw.githubusercontent.com` | 185.199.x.x (Fastly) **10s+ timeout** | **~78ms** | **~130x** |

> **Why not accelerate `github.com`?** GitHub's own server segment (140.82.x.x) was tested but returned HTTP 400 on this network. `github.com` via Azure (20.205.x.x) at ~322ms works fine — the main bottleneck is `raw.githubusercontent.com` which times out at 10s+ via default DNS.

## How It Works

```
User Request
    ↓
DNS Discovery (dig +short — zero port scan risk)
    ↓ (if DNS fails)
Curated IP List (known-good Fastly/GitHub IPs, max 5 probes)
    ↓
Verify via curl + SSL handshake
    ↓
Written to /etc/hosts with marker comments
    ↓
Browser Window Watcher (osascript, 3s interval)
    ↓ (all browser windows closed)
Auto-clean /etc/hosts → restore default DNS
```

### IP Segment Preferences

Each domain is matched to its correct CDN segment:

- **github.com / api.github.com** → GitHub's own server segment (`140.82.x.x`)
- **raw.githubusercontent.com** → Fastly CDN segment (`185.199.x.x`)

## v2.0 What's New

- 🚫 **Zero port scanning risk** — switched from mass TCP scan (9000 IPs, 100 concurrent) to DNS-first + curated IP list
- 🌐 **Multi-browser support** — Edge, Chrome, Safari, or terminal-only mode (`GITHUB_ACCEL_BROWSER`)
- 🧠 **24-hour cache** — was 1 hour, now stays active all day
- 🔁 **Automatic retry** — if scan fails, retries with `--no-cache`
- 🕊️ **Stealth mode** — won't trigger GitHub's WAF or IP bans
- 🎯 **Legacy aggressive mode** — `--aggressive` flag if you really need mass scan

## Requirements

- **macOS** (uses `osascript` for browser window detection)
- **Python 3.6+** (standard library only)
- **sudo** (for `/etc/hosts` write access)
- **A browser** (Edge, Chrome, or Safari — optional, set `GITHUB_ACCEL_BROWSER=none` to skip)
- **curl** (pre-installed on macOS, for IP verification)
- **dig** (pre-installed on macOS, for DNS discovery)

## Project Structure

```
github-accel/
├── github-accel          # Shell script — start/stop/status + browser watcher
├── scripts/
│   └── github-fastip.py  # Python scanner — stealth IP discovery, verify, hosts write
├── docs/
│   ├── ARCHITECTURE.md   # Technical architecture
│   └── CHANGELOG.md      # Version history
├── LICENSE               # MIT
└── README.md             # This file
```

## License

MIT — see [LICENSE](LICENSE).

## Related

- [FastGithub](https://github.com/dotnetcore/FastGithub) — Original .NET C# project that inspired this tool (DNS hijack approach, requires .NET runtime)