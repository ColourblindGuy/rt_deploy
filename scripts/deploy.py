import os
import sys
import time
import paramiko
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RT_IP   = os.environ["RT_TARGET_IP"]
RT_USER = os.environ["RT_FTP_USER"]
RT_PASS = os.environ["RT_FTP_PASS"]

BIN_LOCAL    = Path("releases/bin")
BIN_REMOTE   = "/home/lvuser/natinst/bin"
BACKUP_ROOT  = "/home/lvuser/deploy_backups"


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ---------------------------------------------------------------------------
# SSH helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Remote directory helpers
# ---------------------------------------------------------------------------

def ensure_remote_dir(sftp, path):
    """Create remote directory tree if needed."""
    if path == "/" or path == "":
        return

    parts = path.strip("/").split("/")
    current = ""
    for part in parts:
        current = f"/{part}" if current == "" else f"{current}/{part}"
        try:
            sftp.stat(current)
        except FileNotFoundError:
            log(f"Creating remote directory: {current}")
            sftp.mkdir(current)


# ---------------------------------------------------------------------------
# Recursive remote copy (remote -> remote)
# ---------------------------------------------------------------------------

def recursive_remote_copy(sftp, src, dst):
    """Copy a remote directory tree to another remote directory."""
    ensure_remote_dir(sftp, dst)

    for attr in sftp.listdir_attr(src):
        src_item = f"{src}/{attr.filename}"
        dst_item = f"{dst}/{attr.filename}"

        is_dir = bool(attr.st_mode & 0o40000)

        if is_dir:
            recursive_remote_copy(sftp, src_item, dst_item)
        else:
            log(f"Backing up file: {src_item} -> {dst_item}")
            with sftp.open(src_item, "rb") as fsrc:
                data = fsrc.read()
            with sftp.open(dst_item, "wb") as fdst:
                fdst.write(data)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def backup_remote_bin():
    ssh = open_ssh()
    sftp = ssh.open_sftp()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = f"{BACKUP_ROOT}/bin_{timestamp}"

    log(f"Ensuring backup root exists: {BACKUP_ROOT}")
    ensure_remote_dir(sftp, BACKUP_ROOT)

    log(f"Creating backup folder: {backup_dir}")
    ensure_remote_dir(sftp, backup_dir)

    log("Starting recursive backup...")
    recursive_remote_copy(sftp, BIN_REMOTE, backup_dir)

    sftp.close()
    ssh.close()

    log(f"Backup completed: {backup_dir}")
    return backup_dir


# ---------------------------------------------------------------------------
# Recursive delete
# ---------------------------------------------------------------------------

def clear_remote_folder(sftp, remote_path):
    """Delete all contents of remote folder."""
    for attr in sftp.listdir_attr(remote_path):
        path = f"{remote_path}/{attr.filename}"
        is_dir = bool(attr.st_mode & 0o40000)

        if is_dir:
            clear_remote_folder(sftp, path)
            log(f"Removing folder: {path}")
            sftp.rmdir(path)
        else:
            log(f"Removing file: {path}")
            sftp.remove(path)


# ---------------------------------------------------------------------------
# Upload local bin folder to remote
# ---------------------------------------------------------------------------

def upload_directory_sftp(sftp, local_path, remote_path):
    ensure_remote_dir(sftp, remote_path)

    for item in local_path.iterdir():
        dst = f"{remote_path}/{item.name}"
        if item.is_dir():
            upload_directory_sftp(sftp, item, dst)
        else:
            log(f"Uploading file: {item} -> {dst}")
            sftp.put(str(item), dst)


# ---------------------------------------------------------------------------
# Deploy bin folder (includes backup and rollback)
# ---------------------------------------------------------------------------

def deploy_bin_folder():
    log(f"Deploying bin folder to {RT_IP}")

    # 1. Backup
    backup_dir = backup_remote_bin()

    # 2. Upload new files
    ssh = open_ssh()
    sftp = ssh.open_sftp()

    try:
        log("Clearing remote bin folder...")
        clear_remote_folder(sftp, BIN_REMOTE)

        log("Uploading new bin folder...")
        upload_directory_sftp(sftp, BIN_LOCAL, BIN_REMOTE)

        sftp.close()
        ssh.close()
        log("Upload completed successfully.")

        return backup_dir

    except Exception as e:
        log(f"Upload failed: {e}")
        log("Starting rollback...")

        rollback_from_backup(backup_dir)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def recursive_restore_from_backup(sftp, src, dst):
    ensure_remote_dir(sftp, dst)

    for attr in sftp.listdir_attr(src):
        src_item = f"{src}/{attr.filename}"
        dst_item = f"{dst}/{attr.filename}"
        is_dir = bool(attr.st_mode & 0o40000)

        if is_dir:
            recursive_restore_from_backup(sftp, src_item, dst_item)
        else:
            log(f"Restoring file: {src_item} -> {dst_item}")
            with sftp.open(src_item, "rb") as fsrc:
                data = fsrc.read()
            with sftp.open(dst_item, "wb") as fdst:
                fdst.write(data)


def rollback_from_backup(backup_dir):
    log("Rollback: restoring backup data")

    ssh = open_ssh()
    sftp = ssh.open_sftp()

    clear_remote_folder(sftp, BIN_REMOTE)
    recursive_restore_from_backup(sftp, backup_dir, BIN_REMOTE)

    sftp.close()
    ssh.close()
    log("Rollback completed successfully.")


# ---------------------------------------------------------------------------
# Reboot + Wait Logic
# ---------------------------------------------------------------------------

def reboot_target_via_ssh():
    log("Sending reboot command via SSH...")
    ssh = open_ssh()
    try:
        ssh.exec_command("/sbin/reboot")
        log("Reboot command sent.")
    except Exception:
        log("Reboot disconnect occurred. This is normal.")
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
            log("Target is offline.")
            return True

    log("Warning: target never appeared to go offline.")
    return False


def wait_for_boot(timeout=90):
    log("Waiting for target to come online...")
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            ssh = open_ssh()
            ssh.close()
            log("Target is online again.")
            return True
        except Exception:
            time.sleep(5)

    log("Error: target did not boot in time.")
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log("=== Starting RT Deployment ===")

    try:
        backup_dir = deploy_bin_folder()
    except Exception as e:
        log(f"Deployment failed before reboot: {e}")
        sys.exit(1)

    reboot_target_via_ssh()

    if not wait_for_shutdown():
        log("Shutdown not confirmed. Rolling back.")
        rollback_from_backup(backup_dir)
        sys.exit(1)

    if not wait_for_boot():
        log("Boot failure. Rolling back.")
        rollback_from_backup(backup_dir)
        sys.exit(1)

    log("Deployment completed successfully.")
