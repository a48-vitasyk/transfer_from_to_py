import tkinter as tk
from tkinter import simpledialog, messagebox
import subprocess
import time
import json
import requests
import logging
import os
from paramiko import SSHClient, AutoAddPolicy, ssh_exception



# Создание директории для логов, если она еще не существует
log_directory = "logs"
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

# Настройка пути к файлу лога
log_file_path = os.path.join(log_directory, "rsync_transfer.log")


# Настройка логирования
logging.basicConfig(filename=log_file_path, level=logging.INFO, format='%(asctime)s - %(message)s')


def ask_server_info():
    root = tk.Tk()
    root.withdraw()  # Скрываем основное окно

    server_info = {
        "user": simpledialog.askstring("Input", "Enter your username:", parent=root),
        "host": simpledialog.askstring("Input", "Enter the server host:", parent=root),
        "password": simpledialog.askstring("Input", "Enter your password:", show='*', parent=root),
        "path": simpledialog.askstring("Input", "Enter the path on the server:", parent=root)
    }

    root.destroy()
    return server_info

def show_error_dialog(message):
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Error", message)
    root.destroy()

def check_hash(server, path):
    try:
        client = SSHClient()
        client.set_missing_host_key_policy(AutoAddPolicy())
        client.connect(server['host'], username=server['user'], password=server['password'])
        stdin, stdout, stderr = client.exec_command(f"md5sum {path}")
        hash_sum = stdout.read().split()[0]
        client.close()
        return hash_sum.decode('utf-8')
    except ssh_exception.NoValidConnectionsError as e:
        logging.error(f"Connection error: {e}")
        show_error_dialog(f"Connection error with {server['host']}: {e}")
        return None

def is_rsync_installed():
    try:
        subprocess.run(["rsync", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError:
        logging.error("Rsync is not installed.")
        show_error_dialog("Rsync is not installed on this system.")
        return False

# Перед началом основного цикла, проверим наличие rsync
if not is_rsync_installed():
    exit(1)

def send_slack_notification(message):
    payload = {'text': message}
    requests.post(slack_webhook_url, data=json.dumps(payload), headers={'Content-Type': 'application/json'})

# Получение информации о серверах
servers = {
    "server1": ask_server_info(),
    "server2": ask_server_info()
}

slack_webhook_url = "SLACK_WEBHOOK_URL"
max_retries = 100
fixed_backoff_time = 10

attempt = 0

def run_rsync(source_user, source_host, source_path, destination_user, destination_host, destination_path):
    try:
        rsync_command = [
            "rsync", "-avz",
            f"{source_user}@{source_host}:{source_path}",
            f"{destination_user}@{destination_host}:{destination_path}"
        ]
        result = subprocess.run(rsync_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True, result.stdout.decode()
    except subprocess.CalledProcessError as e:
        logging.error(f"Rsync error: {e.stderr.decode()}")
        return False, e.stderr.decode()

while attempt < max_retries:
    hash_before = check_hash(servers["server1"], servers["server1"]["path"])
    if hash_before is None:
        break

    # Выполнение rsync
    rsync_success, rsync_output = run_rsync(
        servers["server1"]["user"], servers["server1"]["host"], servers["server1"]["path"],
        servers["server2"]["user"], servers["server2"]["host"], servers["server2"]["path"]
    )

    if rsync_success:
        hash_after = check_hash(servers["server2"], servers["server2"]["path"])
        if hash_after is None:
            break

        if hash_before == hash_after:
            message = f"rsync from {servers['server1']['host']} to {servers['server2']['host']} completed successfully, hash match"
            logging.info(message)
            send_slack_notification(message)
            break
        else:
            message = "Hash mismatch error after rsync"
            logging.error(message)
            send_slack_notification(message)
            break
    else:
        message = f"Rsync failure. Attempt {attempt + 1} of {max_retries}. Retrying in {fixed_backoff_time} seconds..."
        logging.warning(message)
        attempt += 1
        time.sleep(fixed_backoff_time)

    if attempt == max_retries:
        error_message = f"Failed to rsync after {max_retries} attempts from {servers['server1']['host']} to {servers['server2']['host']}."
        logging.error(error_message)
        send_slack_notification(error_message)

