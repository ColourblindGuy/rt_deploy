import os
import sys
import time
import requests
import paramiko
from pathlib import Path

RT_IP   = os.environ["RT_TARGET_IP"]
RT_USER = os.environ["RT_FTP_USER"]     # same user (lvuser)
RT_PASS = os.environ["RT_FTP_PASS"]

RTEXE_LOCAL  = Path("releases/MyApp.rtexe")
RTEXE_REMOTE = "/home/lvuser/natinst/bin/MyApp.rtexe"


def scp_upload():
    print(f"Connecting to {RT_IP} via SFTP...")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh.connect(
        RT_IP,
        username=RT_USER,
        password=RT_PASS,
        look_for_keys=False,
        allow_agent=False
    )

    sftp = ssh.open_sftp()
    sftp.put(RTEXE_LOCAL, RTEXE_REMOTE)   # upload file

    sftp.close()
    ssh.close()
    print("SCP upload complete.")


def reboot_target():
    url = f"http://{RT_IP}/nisysapi/server"
    payload = {"Function": "Restart", "Params": {"objSelfURI": f"nisysapi://{RT_IP}"}}
    print("Sending reboot command...")
    try:
        requests.post(url, json=payload, timeout=5)
    except requests.exceptions.ReadTimeout:
        pass  # NI reboots cause immediate disconnect

def reboot_target_via_ssh():
    print("Rebooting target via SSH...")

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh.connect(
        RT_IP,
        username=RT_USER,
        password=RT_PASS,
        look_for_keys=False,
        allow_agent=False
    )

    try:
        # Run reboot command (NI RT allows this without sudo)
        ssh.exec_command("/sbin/reboot")
        print("Reboot command sent.")
    except Exception as e:
        print(f"Ignoring SSH error during reboot: {e}")
    finally:
        ssh.close()
        time.sleep(5)



def wait_for_target(timeout=90):
    print("Waiting for target to come online...")
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(RT_IP, username=RT_USER, password=RT_PASS, timeout=3)
            ssh.close()
            print("Target is back online.")
            return True
        except Exception:
            time.sleep(5)

    print("ERROR: Target did not come back within timeout.")
    return False


def verify_version():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(RT_IP, username=RT_USER, password=RT_PASS)

    sftp = ssh.open_sftp()
    files = sftp.listdir("/home/lvuser/natinst/bin")
    sftp.close()
    ssh.close()

    if "MyApp.rtexe" in files:
        print("Verification passed: RTEXE present on target.")
        return True

    print("Verification FAILED: RTEXE not found on target.")
    return False


if __name__ == "__main__":
    scp_upload()
    reboot_target_via_ssh()

    if not wait_for_target():
        sys.exit(1)

    if not verify_version():
        sys.exit(1)

    print("Deployment successful.")
