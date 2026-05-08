#!/usr/bin/env python3
"""
github-fastip.py — GitHub Access Acceleration Scanner (Stealth Mode)
===================================================================
Scans GitHub's official IP ranges to find the fastest reachable
IP for each domain, then generates /etc/hosts rules.

**Stealth Mode** (default):
- DNS-first discovery: resolve via `dig` before any TCP connect
- Minimal TCP probes: max 5 IPs per domain, sequential, with delay
- Pre-populated curated IP list as fallback
- No mass port scanning — won't trigger GitHub WAF / IP bans

Usage:
  python3 github-fastip.py                          # Scan + write hosts (needs sudo)
  python3 github-fastip.py --scan-only              # Scan only, print results
  python3 github-fastip.py --output <file>          # Output rules to file
  python3 github-fastip.py --status                 # Show acceleration status
  python3 github-fastip.py --restore                # Restore hosts from backup
  python3 github-fastip.py --no-cache               # Force re-scan
  python3 github-fastip.py --aggressive             # Legacy: scan more IPs (may trigger rate limits)

Exit codes:
  0 = OK
  2 = No usable IP found
  3 = Error
"""

import json, socket, sys, time, os, ipaddress, shlex
import threading, argparse
import subprocess, urllib.request
from pathlib import Path

# ─── Configuration ──────────────────────────────────────────────────────────
GITHUB_META_URL = "https://api.github.com/meta"
STEALTH_CONCURRENT = 3        # Max concurrent TCP checks (was 100)
STEALTH_TCP_TIMEOUT = 3.0     # TCP connection timeout (was 1.5)
STEALTH_IPS_PER_DOMAIN = 5    # Max IPs to probe per domain (was 3000)
STEALTH_PROBE_DELAY = 2.0     # Seconds between probes to avoid WAF
CACHE_DIR = os.path.expanduser("~/.cache/github-fastip")
CACHE_FILE = os.path.join(CACHE_DIR, "fastest_ips.json")
CACHE_TTL = 86400              # Cache TTL (24 hours, was 1 hour)
HOSTS_FILE = "/etc/hosts"
BACKUP_FILE = "/etc/hosts.github-fastip.bak"
HOSTS_MARKER = "# --- github-fastip managed ---"

# Curated known-good IPs for GitHub/Fastly CDN
# These are stable and won't trigger WAF
CURATED_IPS = {
    "raw.githubusercontent.com": [
        "185.199.108.133", "185.199.109.133",
        "185.199.110.133", "185.199.111.133",
    ],
    "github.com": [
        "140.82.112.3",   "140.82.112.4",
        "140.82.113.3",   "140.82.114.3",
    ],
    "api.github.com": [
        "140.82.112.6",   "140.82.113.6",
    ],
}

# Domain → preferred IP prefix (for CIDR-based filtering)
DOMAIN_IP_PREFERENCE = {
    "github.com":                     "140.82",
    "api.github.com":                 "140.82",
    "raw.githubusercontent.com":      "185.199",
}

# Domain → hosts aliases
HOSTS_MAP = {
    "github.com":                     ["github.com", "www.github.com"],
    "api.github.com":                 ["api.github.com"],
    "raw.githubusercontent.com":      ["raw.githubusercontent.com", "githubusercontent.com"],
}

# ─── Utility ────────────────────────────────────────────────────────────────
def log(msg):    print(f"[*] {msg}", file=sys.stderr)
def error(msg):  print(f"[!] {msg}", file=sys.stderr)
def ok(msg):     print(f"[✓] {msg}", file=sys.stderr)


# ─── Step 0: DNS Discovery ──────────────────────────────────────────────────
def dns_lookup(domain):
    """Resolve domain via system DNS. Returns list of (ip, rtt_seconds)."""
    try:
        t0 = time.perf_counter()
        result = subprocess.run(
            ["dig", "+short", domain],
            capture_output=True, text=True, timeout=10
        )
        elapsed = time.perf_counter() - t0
        ips = [line.strip() for line in result.stdout.splitlines()
               if line.strip() and not line.startswith(";") and ":" not in line]
        if ips:
            log(f"  DNS resolved {domain}: {ips[0]} (rtt={elapsed*1000:.0f}ms)")
            return [(ip, elapsed) for ip in ips[:3]]
        log(f"  DNS lookup for {domain}: no IPv4 results")
    except Exception as e:
        log(f"  DNS lookup for {domain} failed: {e}")
    return []


# ─── Step 1: Fetch GitHub IP Ranges ──────────────────────────────────────────
def fetch_meta():
    """Fetch GitHub's published IP ranges from the official meta API."""
    log(f"Fetching GitHub IP ranges: {GITHUB_META_URL}")
    req = urllib.request.Request(GITHUB_META_URL, headers={
        "User-Agent": "github-fastip/1.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        error(f"Failed to fetch GitHub meta: {e}")
        return None


# ─── Step 2: Stealth TCP Check ──────────────────────────────────────────────
def tcp_check(ip, timeout=STEALTH_TCP_TIMEOUT):
    """TCP connect test on port 443, returns RTT or None."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        t0 = time.perf_counter()
        r = s.connect_ex((ip, 443))
        elapsed = time.perf_counter() - t0
        s.close()
        return elapsed if r == 0 else None
    except Exception:
        return None


def curl_verify(domain, ip, timeout=5):
    """Verify domain works on given IP via curl --connect-to."""
    try:
        cmd = [
            "curl", "-sI", "--max-time", str(timeout),
            "--connect-to", f"::{ip}",
            f"https://{domain}",
            "-o", "/dev/null", "-w", "%{http_code}",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout+2)
        code = r.stdout.strip()
        if code and code != "000":
            return True, code
    except Exception:
        pass

    # Fallback: Python SSL handshake
    import ssl
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        s = socket.socket()
        s.settimeout(timeout)
        s.connect((ip, 443))
        ss = ctx.wrap_socket(s, server_hostname=domain)
        ss.do_handshake()
        ss.close(); s.close()
        return True, "ssl-ok"
    except Exception:
        pass
    return False, ""


# ─── Scanner ──────────────────────────────────────────────────────────────────
class GithubStealthScan:
    def __init__(self, ips_per_domain=STEALTH_IPS_PER_DOMAIN):
        self.ips_per_domain = ips_per_domain
        self.lock = threading.Lock()
        self.results = {}

    def probe_one(self, domain, ip):
        """Probe a single IP with delay to avoid WAF."""
        rtt = tcp_check(ip, STEALTH_TCP_TIMEOUT)
        time.sleep(STEALTH_PROBE_DELAY)  # polite delay
        if rtt is not None:
            success, code = curl_verify(domain, ip)
            if success:
                return (ip, rtt, code)
        return None

    def scan_domain(self, domain, ips):
        """Sequentially probe IPs with delay. Returns first working one."""
        n = len(ips)
        log(f"Scanning {domain}: {n} IPs...")
        for i, ip in enumerate(ips):
            if i >= self.ips_per_domain:
                break
            result = self.probe_one(domain, ip)
            if result:
                ok(f"{domain} @ {result[0]}: {result[2]} (rtt={result[1]*1000:.0f}ms)")
                self.results[domain] = [result]
                return
        error(f"{domain}: no working IP found within {n} candidates")
        self.results[domain] = []

    def verify_from_dns(self, domain, ips):
        """Try DNS-resolved IPs first, then fall back to curated."""
        candidates = []
        # Try DNS results first
        for ip, rtt in ips:
            candidates.append(ip)
            success, code = curl_verify(domain, ip)
            if success:
                ok(f"{domain} @ {ip}: {code} (via DNS, rtt={rtt*1000:.0f}ms)")
                self.results[domain] = [(ip, rtt, code)]
                return True
        return False

    def get_hosts_lines(self):
        """Generate /etc/hosts lines."""
        lines = [HOSTS_MARKER, "# Generated by github-fastip.py " + time.strftime("%Y-%m-%d %H:%M")]
        for domain, ips in self.results.items():
            if not ips or not ips[0]:
                continue
            ip, rtt, code = ips[0]
            for host in HOSTS_MAP.get(domain, [domain]):
                lines.append(f"{ip}\t{host}")
        lines.append(HOSTS_MARKER)
        return "\n".join(lines) + "\n"


# ─── Hosts Operations ─────────────────────────────────────────────────────────
def backup_hosts():
    try:
        Path(BACKUP_FILE).write_text(Path(HOSTS_FILE).read_text())
        ok(f"Hosts backed up to {BACKUP_FILE}")
    except Exception as e:
        error(f"Backup failed: {e}")

def install_hosts(content):
    try:
        current = Path(HOSTS_FILE).read_text()
    except Exception:
        current = ""
    new_lines = []
    skip = False
    for line in current.split("\n"):
        if HOSTS_MARKER in line:
            skip = not skip
            continue
        if not skip:
            new_lines.append(line)
    while new_lines and new_lines[-1].strip() == "":
        new_lines.pop()
    new_content = "\n".join(new_lines).strip()
    if new_content:
        new_content += "\n\n"
    new_content += content
    try:
        Path(HOSTS_FILE).write_text(new_content)
        ok("Hosts updated")
    except PermissionError:
        error(f"Root permission required to write {HOSTS_FILE}")
        print(f"\nManual install:\n  sudo -E python3 {__file__}", file=sys.stderr)
        print(f"\nOr append to /etc/hosts:\n{content}", file=sys.stderr)
        sys.exit(1)

def restore_hosts():
    if not os.path.exists(BACKUP_FILE):
        error(f"Backup not found: {BACKUP_FILE}")
        sys.exit(1)
    Path(HOSTS_FILE).write_text(Path(BACKUP_FILE).read_text())
    ok("Hosts restored")
    os.remove(BACKUP_FILE)


def show_status():
    try:
        current = Path(HOSTS_FILE).read_text()
    except Exception:
        current = ""
    if HOSTS_MARKER in current:
        print("Acceleration: ACTIVE")
        in_block = False
        for line in current.split("\n"):
            if HOSTS_MARKER in line:
                in_block = not in_block
                continue
            if in_block and line.strip() and not line.startswith("#"):
                p = line.split()
                if len(p) >= 2:
                    print(f"  {p[0]:20s} -> {p[1]}")
    else:
        print("Acceleration: INACTIVE")
    if os.path.exists(CACHE_FILE):
        c = json.loads(Path(CACHE_FILE).read_text())
        age = time.time() - c.get("ts", 0)
        print(f"\n  Last scan: {age/60:.0f} minutes ago")


# ─── Cache ──────────────────────────────────────────────────────────────────
def load_cache():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        c = json.loads(Path(CACHE_FILE).read_text())
        if time.time() - c.get("ts", 0) < CACHE_TTL:
            log(f"Using cache ({int((time.time()-c['ts'])/60)} minutes old)")
            return c
    except Exception:
        pass
    return None

def save_cache(results_dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    Path(CACHE_FILE).write_text(json.dumps({"ts": time.time(), "verified": results_dict}, indent=2))


# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="GitHub Access Acceleration Tool (Stealth Mode)")
    p.add_argument("--scan-only", action="store_true", help="Scan only, don't write hosts")
    p.add_argument("--output", type=str, default="", help="Output rules to file")
    p.add_argument("--no-cache", action="store_true", help="Force re-scan")
    p.add_argument("--status", action="store_true", help="Show acceleration status")
    p.add_argument("--restore", action="store_true", help="Restore hosts from backup")
    p.add_argument("--aggressive", action="store_true",
                    help="Use legacy mass scan (more IPs, may trigger rate limits)")
    args = p.parse_args()

    if args.status:
        show_status()
        return
    if args.restore:
        backup_hosts()
        restore_hosts()
        return

    # Try cache first
    cache = None if args.no_cache else load_cache()
    if cache:
        verified = cache["verified"]
    else:
        scanner = GithubStealthScan()
        domains = ["raw.githubusercontent.com", "github.com", "api.github.com"]

        for domain in domains:
            # Step 0: DNS discovery (zero network risk)
            dns_results = dns_lookup(domain)

            # Step 1: If DNS worked, verify those IPs silently
            if dns_results:
                found = scanner.verify_from_dns(domain, dns_results)
                if found:
                    continue  # DNS gave us a working IP, done

            # Step 2: Try curated IPs (safe, no port scan needed if TCP verify passes)
            curated = CURATED_IPS.get(domain, [])
            if curated:
                scanner.scan_domain(domain, curated)
            else:
                error(f"{domain}: no curated IPs available, skipping")
                scanner.results[domain] = []

        verified = {}
        for domain in domains:
            if scanner.results.get(domain):
                verified[domain] = [
                    [scanner.results[domain][0][0],
                     scanner.results[domain][0][1],
                     scanner.results[domain][0][2]]
                ]

        save_cache(verified)

    # Generate hosts content
    scanner = GithubStealthScan()
    for domain, entries in (verified or {}).items():
        if entries:
            scanner.results[domain] = entries
        else:
            scanner.results[domain] = []

    hosts_content = scanner.get_hosts_lines()

    if args.output:
        Path(args.output).write_text(hosts_content)
        ok(f"Rules written to {args.output}")
    elif args.scan_only:
        print(hosts_content)
    else:
        backup_hosts()
        install_hosts(hosts_content)

    # Summary
    print("\n" + "=" * 40, file=sys.stderr)
    print("GitHub Acceleration Summary:", file=sys.stderr)
    for domain in domains:
        entry = (verified or {}).get(domain)
        if entry:
            print(f"  {domain} -> {entry[0][0]} ({entry[0][1]*1000:.0f}ms)", file=sys.stderr)
        else:
            print(f"  {domain} -> NONE (failed)", file=sys.stderr)
    print("=" * 40, file=sys.stderr)


if __name__ == "__main__":
    main()