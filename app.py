import json
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

import psutil
from flask import Flask, jsonify, render_template, request

# --- ІСНУЮЧА КОНФІГУРАЦІЯ ---
MAX_DATA_POINTS = 1200
UPDATE_INTERVAL = 0.1
app = Flask(__name__)
data_points = {
    "timestamps": deque(maxlen=MAX_DATA_POINTS), "cpu_temp": deque(maxlen=MAX_DATA_POINTS),
    "cpu_load": deque(maxlen=MAX_DATA_POINTS), "gpu_temp": deque(maxlen=MAX_DATA_POINTS),
    "gpu_load": deque(maxlen=MAX_DATA_POINTS),
}
latest_ram_usage = 0

# --- ІСНУЮЧІ ФУНКЦІЇ (без змін) ---
def get_cpu_temperature():
    try:
        temps = psutil.sensors_temperatures()
        if 'k10temp' in temps: return temps['k10temp'][0].current
        if 'coretemp' in temps: return temps['coretemp'][0].current
    except (AttributeError, KeyError): return 0
    return 0

def get_gpu_metrics():
    try:
        command = "nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader,nounits"
        result = subprocess.check_output(command, shell=True, text=True)
        temp_str, load_str = result.strip().split(', ')
        return int(temp_str), int(load_str)
    except Exception: return 0, 0

def collect_metrics():
    global latest_ram_usage
    while True:
        cpu_load = psutil.cpu_percent(interval=None)
        cpu_temp = get_cpu_temperature()
        gpu_temp, gpu_load = get_gpu_metrics()
        ram_usage = psutil.virtual_memory().percent
        timestamp = datetime.now().strftime("%H:%M:%S")
        data_points["timestamps"].append(timestamp)
        data_points["cpu_temp"].append(cpu_temp)
        data_points["cpu_load"].append(cpu_load)
        data_points["gpu_temp"].append(gpu_temp)
        data_points["gpu_load"].append(gpu_load)
        latest_ram_usage = ram_usage
        time.sleep(UPDATE_INTERVAL)

# --- ІСНУЮЧІ ENDPOINTS (без змін) ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/data')
def get_data():
    response_data = {key: list(value) for key, value in data_points.items()}
    response_data["ram_usage"] = latest_ram_usage
    return jsonify(response_data)

# --- НОВІ ENDPOINTS ДЛЯ МЕНЕДЖЕРА ПРОЦЕСІВ ---
@app.route('/processes')
def get_processes():
    """Повертає список активних процесів з основними показниками."""
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent']):
        try:
            # Отримуємо інформацію про процес
            p_info = proc.as_dict(attrs=['pid', 'name', 'username', 'cpu_percent', 'memory_percent'])
            # Для форматування округлюємо значення
            p_info['memory_percent'] = round(p_info['memory_percent'], 2)
            p_info['cpu_percent'] = round(p_info['cpu_percent'], 2)
            processes.append(p_info)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass # Ігноруємо процеси, до яких немає доступу або які вже не існують
    return jsonify(processes)

@app.route('/kill/<int:pid>', methods=['POST'])
def kill_process(pid):
    """Завершує процес за його PID."""
    try:
        if request.method == 'POST':
            proc = psutil.Process(pid)
            proc.terminate() # М'яке завершення
            # proc.kill() # Примусове завершення
            return jsonify({"status": "success", "message": f"Process {pid} terminated."})
    except psutil.NoSuchProcess:
        return jsonify({"status": "error", "message": "Process not found."}), 404
    except psutil.AccessDenied:
        return jsonify({"status": "error", "message": "Permission denied."}), 403
    return jsonify({"status": "error", "message": "Invalid request."}), 400


if __name__ == '__main__':
    metrics_thread = threading.Thread(target=collect_metrics, daemon=True)
    metrics_thread.start()
    app.run(host='0.0.0.0', port=5000)