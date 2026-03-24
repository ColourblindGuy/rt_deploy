import os
import sys
import time
import requests
import paramiko
from pathlib import Path
from datetime import datetime

RT_IP   = os.environ["RT_TARGET_IP"]
RT_USER = os.environ["RT_FTP_USER"]
RT_PASS = os.environ["RT_FTP_PASS"]

BIN_LOCAL  = Path("releases/bin")
BIN_REMOTE = "/home/lvuser/natinst/bin"
BACKUP_ROOT = "/home/lvuser/deploy_backups"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def open_ssh():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(
        RT_IP,
        username=RT_USER,
        password=RT_PASS,
        look_for_keys=False,
        allow_agent=False,
        timeout=5
    )
    return ssh


def upload_directory_sftp(sftp, local_path: Path, remote_path: str):
    try:
        sftp.stat(remote_path)
    except FileNotFoundError:
        log(f"Creating remote directory: {remote_path}")
        sftp.mkdir(remote_path)

    for item in local_path.iterdir():
        remote_item = f"{remote_path}/{item.name}"
        if item.is_dir():
            upload_directory_sftp(sftp, item, remote_item)
        else:
            log(f"Uploading file: {item} → {remote_item}")
            sftp.put(str(item), remote_item)



def clear_remote_folder(sftp, remote_path):
    for attr in sftp.listdir_attr(remote_path):
        rpath = f"{remote_path}/{attr.filename}"

        if attr.st_mode & 0o40000:  # directory bit
            clear_remote_folder(sftp, rpath)
            log(f"Removing folder: {rpath}")
            sftp.rmdir(rpath)
        else:
            log(f"Removing file: {rpath}")
            sftp.remove(rpath)



def backup_remote_bin():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = f"{BACKUP_ROOT}/bin_{timestamp}"

    ssh = open_ssh()
    sftp = ssh.open_sftp()

    # Ensure backup root exists
    try:
        sftp.stat(BACKUP_ROOT)
    except FileNotFoundError:
        log(f"Creating backup root: {BACKUP_ROOT}")
        sftp.mkdir(BACKUP_ROOT)

    log(f"Creating backup: {backup_dir}")
    sftp.mkdir(backup_dir)

    def recursive_copy(remote_src, remote_dst):
        sftp.mkdir(remote_dst)
        for attr in sftp.listdir_attr(remote_src):
            src = f"{remote_src}/{attr.filename}"
            dst = f"{remote_dst}/{attr.filename}"

            if attr.st_mode & 0o40000:  # directory
                recursive_copy(src, dst)
            else:
                sftp.get(src, f"/tmp/{attr.filename}")  # temp local copy
                sftp.put(f"/tmp/{attr.filename}", dst)

    recursive_copy(BIN_REMOTE, backup_dir)

    ssh.close()
    return backup_dir




def rollback_from_backup(backup_dir):
    log("ROLLBACK: Restoring previous bin folder...")
    ssh = open_ssh()
    sftp = ssh.open_sftp()

    clear_remote_folder(sftp, BIN_REMOTE)
    upload_directory_sftp(sftp, Path(f"/tmp/rollback"), BIN_REMOTE)

    log("Rollback completed.")

    sftp.close()
    ssh.close()



def deploy_bin_folder():
    log(f"Deploying full bin folder to {RT_IP}...")

    ssh = open_ssh()
    sftp = ssh.open_sftp()

    # Backup
    log("Creating backup of current /bin folder...")
    backup_dir = backup_remote_bin()

    try:
        log("Clearing /bin folder on target...")
        clear_remote_folder(sftp, BIN_REMOTE)

        log("Uploading new bin folder...")
        upload_directory_sftp(sftp, BIN_LOCAL, BIN_REMOTE)

        log("✅ Deployment upload completed.")
        sftp.close()
        ssh.close()

        return backup_dir

    except Exception as e:
        log(f"❌ ERROR during upload: {e}")
        log("Attempting rollback...")

        rollback_from_backup(backup_dir)

        sys.exit(1)



def reboot_target_via_ssh():
    log("Rebooting target via SSH...")
    ssh = open_ssh()
    try:
        ssh.exec_command("/sbin/reboot")
        log("Reboot command sent.")
    except Exception as e:
        log(f"Ignoring reboot disconnect: {e}")
    finally:
        ssh.close()

def wait_for_shutdown(timeout=30):
    log("Waiting for target to shut down...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ssh = open_ssh()
            ssh.close()
            time.sleep(2)
        except Exception:
            log("✅ Target offline.")
            return True
    log("WARNING: Target never appeared offline.")
    return False

def wait_for_boot(timeout=90):
    log("Waiting for target to boot...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ssh = open_ssh()
            ssh.close()
            log("✅ Target online.")
            return True
        except Exception:
            time.sleep(5)
    log("❌ Boot timeout!")
    return False



if __name__ == "__main__":
    backup_dir = deploy_bin_folder()

    reboot_target_via_ssh()
    wait_for_shutdown()

    if not wait_for_boot():
        log("Boot failed, rolling back...")
        rollback_from_backup(backup_dir)
        sys.exit(1)

    log("✅ Deployment successful.")
