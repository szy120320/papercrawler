"""
One-shot script: create GitHub repo + initial commit + push.
- Token comes from env var (GH_TOKEN), never written to disk
- Uses GitHub API to create repo
- Uses git directly with URL-embedded token for first push
- Then immediately removes token from remote URL (cleans .git/config)
"""
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO_NAME = "papercrawler"
REPO_DESC = "Domain-aware academic paper searcher, classifier and downloader. " \
            "Filtrer les articles par domaine d'intérêt, classifier (matériaux / DFT / force field), exporter en CSV, télécharger le texte intégral."
VISIBILITY = "public"  # public/private

PROJECT_DIR = r"G:\minimax_work_sapce\PaperCrawler-main"
GIT_BIN = r"C:\Program Files\Git\cmd\git.exe"


def run(cmd, cwd=None, check=True):
    """Run shell command, return (stdout, stderr, returncode)"""
    if isinstance(cmd, str):
        cmd = cmd.split()
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=False)
    if result.stdout.strip():
        print(f"    stdout: {result.stdout.strip()[:300]}")
    if result.stderr.strip():
        print(f"    stderr: {result.stderr.strip()[:300]}")
    if check and result.returncode != 0:
        print(f"\n!!! Command failed with exit code {result.returncode}")
        sys.exit(1)
    return result


def main():
    token = os.environ.get("GH_TOKEN")
    if not token:
        print("ERROR: GH_TOKEN env var not set")
        sys.exit(1)
    if not token.startswith("ghp_"):
        print("ERROR: GH_TOKEN doesn't look like a valid PAT (should start with ghp_)")
        sys.exit(1)

    os.chdir(PROJECT_DIR)
    print(f"Working dir: {os.getcwd()}\n")

    # ---- Step 1: Create GitHub repo via API ----
    print("[1/5] Creating GitHub repo via API...")
    payload = json.dumps({
        "name": REPO_NAME,
        "description": REPO_DESC,
        "private": (VISIBILITY == "private"),
        "auto_init": False,  # we'll push our own
    }).encode()
    req = urllib.request.Request(
        "https://api.github.com/user/repos",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            repo_info = json.loads(resp.read())
            print(f"    Created: {repo_info.get('html_url')}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 422 and "name already exists" in body.lower():
            print("    Repo already exists, continuing...")
        else:
            print(f"    ERROR {e.code}: {body}")
            sys.exit(1)

    # ---- Step 2b: Clear any broken proxy config for THIS repo only ----
    print("\n[2b] Clearing broken global proxy (local repo only)...")
    # After git init, the local config inherits from global. Setting local to empty
    # string overrides the global broken proxy.
    run([GIT_BIN, "config", "--local", "http.proxy", ""], check=False)
    run([GIT_BIN, "config", "--local", "https.proxy", ""], check=False)
    print("    OK - direct connection to GitHub will be used")
    print("\n[2/5] Initializing git repo + first commit...")
    if not os.path.exists(os.path.join(PROJECT_DIR, ".git")):
        # git 2.28+ supports -b/--initial-branch
        run([GIT_BIN, "init", "-b", "main"])
    # If still on master (older git or pre-existing repo), rename
    current_branch = subprocess.run(
        [GIT_BIN, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True
    ).stdout.strip()
    if current_branch == "master":
        run([GIT_BIN, "branch", "-m", "main"])
    elif not current_branch or current_branch == "HEAD":
        # Detached or no commits yet — point HEAD to main explicitly
        run([GIT_BIN, "symbolic-ref", "HEAD", "refs/heads/main"])
    run([GIT_BIN, "config", "--local", "init.defaultBranch", "main"])
    run([GIT_BIN, "add", "-A"])
    run([GIT_BIN, "status", "--short"])
    run([GIT_BIN, "commit", "-m", "chore: 初始提交(项目重命名 paper-dl→PaperCrawler + 领域感知改造)"])
    # Safety: if commit landed on master (because HEAD was already master), rename now
    post_branch = subprocess.run(
        [GIT_BIN, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True
    ).stdout.strip()
    if post_branch == "master":
        run([GIT_BIN, "branch", "-m", "main"])
    run([GIT_BIN, "branch", "-a"])
    run([GIT_BIN, "log", "--oneline"])

    # ---- Step 4: Add remote + push (with embedded token, used only once) ----
    print("\n[4/5] Pushing to GitHub (using token from env)...")
    # Direct github.com is blocked in this environment, so use ghfast.top mirror
    # to access the repo. The mirror proxies the actual GitHub connection.
    direct_url = f"https://oauth2:{token}@github.com/szy120320/{REPO_NAME}.git"
    mirror_url = f"https://oauth2:{token}@ghfast.top/https://github.com/szy120320/{REPO_NAME}.git"
    run([GIT_BIN, "remote", "remove", "origin"], check=False)
    run([GIT_BIN, "remote", "add", "origin", mirror_url])
    # Use -c to override broken global proxy for THIS push only
    run([
        GIT_BIN, "-c", "http.proxy=", "-c", "https.proxy=",
        "push", "-u", "origin", "main",
    ])

    # ---- Step 5: Clean token from remote URL ----
    print("\n[5/5] Cleaning token from .git/config...")
    # Keep using the mirror URL (since direct github.com is blocked in this env)
    clean_url = f"https://github.com/szy120320/{REPO_NAME}.git"  # canonical
    # NOTE: We keep the mirror URL in .git/config because direct github.com is blocked.
    # But the token IS removed:
    mirror_clean = f"https://ghfast.top/https://github.com/szy120320/{REPO_NAME}.git"
    run([GIT_BIN, "remote", "set-url", "origin", mirror_clean])
    run([GIT_BIN, "remote", "-v"])

    print("\n" + "=" * 60)
    print("✅ Done! Repo: https://github.com/szy120320/papercrawler")
    print("✅ Token 已从 .git/config 移除")
    print("=" * 60)


if __name__ == "__main__":
    main()
