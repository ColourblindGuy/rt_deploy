import ftplib
import os
import sys
import time
import requests
from pathlib import Path

RT_IP   = os.environ["RT_TARGET_IP"]
RT_USER = os.environ["RT_FTP_USER"]
RT_PASS = os.environ["RT_FTP_PASS"]

RTEXE_LOCAL  = Path("releases/MyApp.rtexe")   # adjust to your file path
RTEXE_REMOTE = "/home/lvuser/natinst/bin/MyApp.rtexe"   # standard NI RT deploy path

def ftp_upload():
    print(f"Connecting to {RT_IP} via FTP...")
    with ftplib.FTP(RT_IP, RT_USER, RT_PASS) as ftp:
        ftp.set_pasv(True)
        with open(RTEXE_LOCAL, "rb") as f:
            ftp.storbinary(f"STOR {RTEXE_REMOTE}", f)
    print("Upload complete.")

def reboot_target():
    # NI Web-based Configuration and Monitoring (WBCM) REST API
    url = f"http://{RT_IP}/nisysapi/server"
    payload = {"Function": "Restart", "Params": {"objSelfURI": f"nisysapi://{RT_IP}"}}
    print("Sending reboot command...")
    try:
        requests.post(url, json=payload, timeout=5)
    except requests.exceptions.ReadTimeout:
        pass  # timeout is expected — target is rebooting

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
    # Read a version file you write from your RT app, or check a known file timestamp
    with ftplib.FTP(RT_IP, RT_USER, RT_PASS) as ftp:
        files = ftp.nlst("/home/lvuser/natinst/bin")
        if "MyApp.rtexe" in [f.split("/")[-1] for f in files]:
            print("Verification passed: RTEXE present on target.")
            return True
    print("Verification FAILED: RTEXE not found on target.")
    return False

if __name__ == "__main__":
    ftp_upload()
    reboot_target()
    ok = wait_for_target()
    if not ok:
        sys.exit(1)
    ok = verify_version()
    if not ok:
        sys.exit(1)
    print("Deployment successful.")
