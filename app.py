import json
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

import psutil
from flask import Flask, jsonify, render_template

# КОНФІГУРАЦІЯ
MAX_DATA_POINTS = 1200  # 1200 точок (2 хвилини при інтервалі 0.1с)
UPDATE_INTERVAL = 0.1   # Інтервал оновлення даних в секундах (10 разів/сек)

# Ініціалізація Flask
app = Flask(__name__)

# Сховище для даних моніторингу
# deque автоматично видаляє старі дані, коли досягає maxlen
data_points = {
    "timestamps": deque(maxlen=MAX_DATA_POINTS),
    "cpu_temp": deque(maxlen=MAX_DATA_POINTS),
    "cpu_load": deque(maxlen=MAX_DATA_POINTS),
    "gpu_temp": deque(maxlen=MAX_DATA_POINTS),
    "gpu_load": deque(maxlen=MAX_DATA_POINTS),
}
# Окремо зберігаємо останнє значення RAM
latest_ram_usage = 0

def get_cpu_temperature():
    """Отримує температуру CPU, шукаючи сенсор AMD."""
    try:
        temps = psutil.sensors_temperatures()
        # Поширений ключ для датчиків температури AMD на Linux
        if 'k10temp' in temps:
            for entry in temps['k10temp']:
                return entry.current
        # Резервний варіант для інших систем (напр. Intel)
        if 'coretemp' in temps:
            return temps['coretemp'][0].current
    except (AttributeError, KeyError):
        # Повертає 0, якщо дані про температуру недоступні
        return 0
    return 0

def get_gpu_metrics():
    """Отримує температуру та навантаження GPU NVIDIA через nvidia-smi."""
    try:
        # Запит лише потрібних даних у форматі CSV для легкого парсингу
        command = "nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader,nounits"
        result = subprocess.check_output(command, shell=True, text=True)
        temp_str, load_str = result.strip().split(', ')
        return int(temp_str), int(load_str)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        # Повертає (0, 0), якщо nvidia-smi не знайдено або сталася помилка
        return 0, 0

def collect_metrics():
    """
    Функція, що безперервно збирає метрики у фоновому потоці.
    """
    global latest_ram_usage
    while True:
        # Збір даних
        cpu_load = psutil.cpu_percent(interval=None)
        cpu_temp = get_cpu_temperature()
        gpu_temp, gpu_load = get_gpu_metrics()
        ram_usage = psutil.virtual_memory().percent

        # Додавання даних до сховища
        timestamp = datetime.now().strftime("%H:%M:%S")
        data_points["timestamps"].append(timestamp)
        data_points["cpu_temp"].append(cpu_temp)
        data_points["cpu_load"].append(cpu_load)
        data_points["gpu_temp"].append(gpu_temp)
        data_points["gpu_load"].append(gpu_load)

        latest_ram_usage = ram_usage
        
        # Очікування перед наступним збором
        time.sleep(UPDATE_INTERVAL)

@app.route('/')
def index():
    """Головна сторінка, що віддає веб-інтерфейс."""
    return render_template('index.html')

@app.route('/data')
def get_data():
    """Endpoint для передачі зібраних даних у форматі JSON."""
    # Перетворення deque в списки для серіалізації в JSON
    response_data = {key: list(value) for key, value in data_points.items()}
    response_data["ram_usage"] = latest_ram_usage
    return jsonify(response_data)

if __name__ == '__main__':
    # Запуск збору метрик у фоновому потоці
    metrics_thread = threading.Thread(target=collect_metrics, daemon=True)
    metrics_thread.start()
    
    # Запуск веб-сервера Flask
    # host='0.0.0.0' робить сервер доступним у локальній мережі
    app.run(host='0.0.0.0', port=5000)