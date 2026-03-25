# LabVIEW RT Deployment Pipeline

Automated CI/CD pipeline for deploying LabVIEW Real-Time applications to CompactRIO and PXIe targets using GitHub Actions and a self-hosted runner.

---

## Table of contents

- [Overview](#overview)
- [Repository structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
  - [1. Register the self-hosted runner](#1-register-the-self-hosted-runner)
  - [2. Configure GitHub secrets](#2-configure-github-secrets)
  - [3. Add the deploy script](#3-add-the-deploy-script)
  - [4. Add the workflow file](#4-add-the-workflow-file)
- [How to deploy a new version](#how-to-deploy-a-new-version)
- [How rollback works](#how-rollback-works)
- [Workflow reference](#workflow-reference)
- [Troubleshooting](#troubleshooting)

---

## Overview

Pushing a new `.rtexe` build to the `releases` branch automatically:

1. Triggers the GitHub Actions workflow
2. Routes the job to the self-hosted runner on your lab network
3. Transfers the RTEXE to the RT target via FTP
4. Reboots the target to load the new application
5. Verifies the target came back online with the correct file

```
GitHub (releases branch)
        │
        │  HTTPS (outbound, runner polls GitHub)
        ▼
Self-hosted runner (Windows PC, lab LAN)
        │
        │  FTP port 21 (LAN)
        ▼
RT Target (cRIO / PXIe)
```

The HMI application on the connected host PC is deployed in the same workflow run, keeping both versions in sync.

---

## Repository structure

```
your-repo/
├── .github/
│   └── workflows/
│       └── deploy-rt.yml       # GitHub Actions workflow definition
├── scripts/
│   └── deploy_rt.py            # Python deploy script (FTP + reboot + verify)
├── releases/
│   └── MyApp.rtexe             # Built RTEXE — push here to trigger deploy
├── .gitattributes              # Marks binary files correctly
└── README.md
```

> **Note:** The `.github/workflows/` path is required exactly as shown. GitHub only scans that location for workflow files.

---

## Prerequisites

| Requirement | Details |
|---|---|
| GitHub repository | Any visibility. Self-hosted runners work with free accounts. |
| Self-hosted runner machine | Windows PC on the same LAN as your RT targets. Always-on. |
| Python 3.11+ | Installed on the runner machine, available on `PATH`. |
| RT target network access | Runner must reach the target IP on FTP port 21. |
| NI RT Web Services enabled | Required for the reboot API call. Enable in NI MAX. |

---

## Setup

### 1. Register the self-hosted runner

On your runner machine, open PowerShell and run the following. Replace the URL and token with values from **Settings → Actions → Runners → New self-hosted runner** in your GitHub repo.

```powershell
# Create runner directory
mkdir C:\actions-runner
cd C:\actions-runner

# Download runner (use the URL GitHub provides — version may differ)
Invoke-WebRequest -Uri https://github.com/actions/runner/releases/download/v2.x.x/actions-runner-win-x64-2.x.x.zip -OutFile runner.zip
Expand-Archive runner.zip -DestinationPath .

# Register with your repo
.\config.cmd --url https://github.com/YOUR_ORG/YOUR_REPO --token YOUR_REGISTRATION_TOKEN

# Install and start as a Windows service (survives reboots)
.\svc.cmd install
.\svc.cmd start
```

After registration the runner appears as **Idle** under Settings → Actions → Runners. It connects to GitHub over outbound HTTPS — no inbound firewall rules are needed.

---

### 2. Configure GitHub secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add the following:

| Secret name | Example value | Description |
|---|---|---|
| `RT_TARGET_IP` | `192.168.1.100` | IP address of the RT target |
| `RT_FTP_USER` | `admin` | FTP username (NI RT default: `admin`) |
| `RT_FTP_PASS` | `yourpassword` | FTP password |

These are injected as environment variables at runtime and are never exposed in workflow logs.

---

### 3. Add the deploy script

Create `scripts/deploy_rt.py` in your repository:

```python
import ftplib
import os
import sys
import time
import requests
from pathlib import Path

RT_IP   = os.environ["RT_TARGET_IP"]
RT_USER = os.environ["RT_FTP_USER"]
RT_PASS = os.environ["RT_FTP_PASS"]

RTEXE_LOCAL  = Path("releases/MyApp.rtexe")
RTEXE_REMOTE = "/ni-rt/startup/MyApp.rtexe"

def ftp_upload():
    print(f"Connecting to {RT_IP} via FTP...")
    with ftplib.FTP(RT_IP, RT_USER, RT_PASS) as ftp:
        ftp.set_pasv(True)
        with open(RTEXE_LOCAL, "rb") as f:
            ftp.storbinary(f"STOR {RTEXE_REMOTE}", f)
    print("Upload complete.")

def reboot_target():
    url = f"http://{RT_IP}/nisysapi/server"
    payload = {"Function": "Restart", "Params": {"objSelfURI": f"nisysapi://{RT_IP}"}}
    print("Sending reboot command...")
    try:
        requests.post(url, json=payload, timeout=5)
    except requests.exceptions.ReadTimeout:
        pass  # expected — target is rebooting

def wait_for_target(timeout=90):
    print("Waiting for target to come back online...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ftplib.FTP(RT_IP, RT_USER, RT_PASS).quit()
            print("Target is back online.")
            return True
        except Exception:
            time.sleep(5)
    print("ERROR: Target did not come back within timeout.")
    return False

def verify_version():
    with ftplib.FTP(RT_IP, RT_USER, RT_PASS) as ftp:
        files = ftp.nlst("/ni-rt/startup/")
        if "MyApp.rtexe" in [f.split("/")[-1] for f in files]:
            print("Verification passed: RTEXE present on target.")
            return True
    print("Verification FAILED.")
    return False

if __name__ == "__main__":
    ftp_upload()
    reboot_target()
    if not wait_for_target():
        sys.exit(1)
    if not verify_version():
        sys.exit(1)
    print("Deployment successful.")
```

Adjust `RTEXE_LOCAL` and `RTEXE_REMOTE` to match your application name and target path.

---

### 4. Add the workflow file

Create `.github/workflows/deploy-rt.yml`:

```yaml
name: Deploy to RT target

on:
  push:
    branches:
      - releases

jobs:
  deploy:
    runs-on: self-hosted

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install requests

      - name: Deploy RTEXE to target
        env:
          RT_TARGET_IP: ${{ secrets.RT_TARGET_IP }}
          RT_FTP_USER:  ${{ secrets.RT_FTP_USER }}
          RT_FTP_PASS:  ${{ secrets.RT_FTP_PASS }}
        run: python scripts/deploy_rt.py

      - name: Notify on failure
        if: failure()
        run: echo "Deployment failed — check logs above."
```

Commit both files to `main` and push:

```bash
git add .github/workflows/deploy-rt.yml scripts/deploy_rt.py
git commit -m "Add RT deploy pipeline"
git push origin main
```

Verify the workflow appears under the **Actions** tab in GitHub before proceeding.

---

## How to deploy a new version

1. Build your LabVIEW RT application and export the `.rtexe`
2. Copy the file into the `releases/` folder of your local repo clone
3. Commit and push to the `releases` branch:

```bash
git checkout releases
git add releases/MyApp.rtexe
git commit -m "Release v1.2.0"
git push origin releases
```

4. Open the **Actions** tab in GitHub to monitor progress in real time
5. A green checkmark means the target is running the new version. A red X means a step failed — click into the run to see full log output.

---

## How rollback works

Every previous RTEXE is preserved in the `releases` branch git history. To roll back to a previous version:

```bash
# Find the commit hash of the version you want
git log releases --oneline

# Check out that version of the RTEXE
git checkout releases
git checkout <commit-hash> -- releases/MyApp.rtexe

# Commit and push — this triggers a deploy of the old version
git commit -m "Rollback to v1.1.0"
git push origin releases
```

A web-based rollback UI is planned as a future addition, allowing one-click version selection without using the command line.

---

## Workflow reference

| Field | Value | Purpose |
|---|---|---|
| `on: push: branches: releases` | `releases` | Only deploys from this branch |
| `runs-on` | `self-hosted` | Routes job to your lab runner |
| `actions/checkout@v4` | — | Clones repo onto runner at current commit |
| `actions/setup-python@v5` | `3.11` | Ensures correct Python version |
| `env: secrets.*` | GitHub Secrets | Injects credentials without exposing them in logs |
| `if: failure()` | — | Failure handler — runs only when a step fails |

---

## Troubleshooting

**Workflow does not appear in the Actions tab**
The `.github/workflows/` folder path must be exact, including the leading dot. Check for typos.

**Runner shows as offline**
Verify the runner service is running on the middleman PC: open Services (`services.msc`) and look for `GitHub Actions Runner`. It should be status Running. If it stopped, start it and check the runner logs at `C:\actions-runner\_diag\`.

**FTP connection refused**
Confirm FTP is enabled on the RT target in NI MAX (Remote Systems → your target → Network Settings → FTP enabled). Also verify the runner machine can reach the target IP — run `ping <RT_IP>` from the runner.

**Reboot command times out or fails**
Verify NI Web Services are enabled on the target in NI MAX. The reboot API uses HTTP port 80 on the target — check that port is not blocked by a firewall between the runner and the target subnet.

**Target does not come back online within 90 seconds**
Some targets take longer to boot depending on FPGA bitfile complexity. Increase the `timeout` parameter in `wait_for_target()` in `deploy_rt.py`.

**RTEXE not found after reboot**
Check the `RTEXE_REMOTE` path in `deploy_rt.py`. The standard NI RT startup path is `/ni-rt/startup/` but confirm this on your specific target using NI MAX → File Transfer.
