"""Test GitHub connectivity 3 ways."""
import json
import os
import subprocess
import urllib.error
import urllib.request

token = os.environ.get("GH_TOKEN", "")
GIT_BIN = r"C:\Program Files\Git\cmd\git.exe"

print("--- Test 1: GitHub API /user (auth check) ---")
try:
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "papercrawler-setup"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
        print(f"  OK: user={data.get('login')} id={data.get('id')}")
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")

print("\n--- Test 2: Repo GET /repos/szy120320/papercrawler ---")
try:
    req = urllib.request.Request(
        "https://api.github.com/repos/szy120320/papercrawler",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "papercrawler-setup"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
        print(f"  OK: {data.get('html_url')} private={data.get('private')}")
except urllib.error.HTTPError as e:
    body = e.read().decode()[:300]
    print(f"  HTTP {e.code}: {body}")
except Exception as e:
    print(f"  FAIL: {type(e).__name__}: {e}")

print("\n--- Test 3: git ls-remote https://github.com/szy120320/papercrawler.git ---")
r = subprocess.run(
    [GIT_BIN, "-c", "http.proxy=", "-c", "https.proxy=",
     "ls-remote", "https://github.com/szy120320/papercrawler.git"],
    capture_output=True, text=True, timeout=30,
)
print(f"  exit={r.returncode}")
print(f"  stdout: {r.stdout[:200]}")
print(f"  stderr: {r.stderr[:500]}")
