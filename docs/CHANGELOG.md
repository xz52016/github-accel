# Changelog

## [2.0.0] — 2026-05-08

### Added
- **Stealth Mode** — DNS-first discovery (`dig +short`) before any TCP connection. Eliminates mass port scanning risk.
- **Multi-browser watcher** — `GITHUB_ACCEL_BROWSER=edge|chrome|safari|none` environment variable.
  - Edge (default), Chrome, Safari supported via `osascript`
  - `none` mode for terminal-only usage (no watcher, manual clean)
- **Automatic retry** — if scan returns empty, retries with `--no-cache`
- **`--aggressive` flag** — legacy mass scan mode for advanced users (may trigger rate limits)
- **Curated IP fallback list** — known-good Fastly (185.199.108-111.x) and GitHub (140.82.112-114.x) IPs

### Changed
- **Massively reduced scan surface**: 3000 IPs/domain → 5 IPs/domain
- **Concurrency reduced**: 100 threads → 3 threads
- **Probe delay added**: 2 seconds between each TCP attempt
- **Cache TTL increased**: 1 hour → 24 hours
- **TCP timeout increased**: 1.5s → 3.0s (better reliability on slow networks)
- **Script path fix**: `github-accel` bash script now correctly references `scripts/github-fastip.py`

### Removed
- TCP-only fallback verification (unreliable, removed in favor of SSL+curl verification)
- Concurrent mass scanning (replaced by sequential stealth probes)

### Fixed
- Bash script path mismatch (line 10) — caused runtime error "file not found"

## [1.0.0] — 2026-05-05

### Added
- Initial release
- `github-accel` switch script — start/stop/status with Edge browser window watcher
- `github-fastip.py` — concurrent TCP scanner for GitHub CIDR ranges
- Domain-aware IP segment preference:
  - `github.com` / `api.github.com` → GitHub's own server segment (140.82.x.x)
  - `raw.githubusercontent.com` → Fastly CDN segment (185.199.x.x)
- Triple verification pipeline: curl → Python SSL → TCP fallback
- Auto-backup and restore of `/etc/hosts`
- 1-hour cache with `--no-cache` override
- Auto-disconnect: monitors Microsoft Edge windows via `osascript`, clears hosts when all windows close
- Real benchmark: raw.githubusercontent.com — 10s+ → 78ms (**~130x improvement**)