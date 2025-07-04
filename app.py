import json
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

import psutil
from flask import Flask, jsonify, render_template, request

# Для керування аудіо (Linux - PulseAudio)
try:
    import pulsectl
except ImportError:
    pulsectl = None
    print("Попередження: бібліотека 'pulsectl' не знайдена. Керування аудіо буде вимкнено. Встановіть за допомогою 'pip install pulsectl'")


# КОНФІГУРАЦІЯ
MAX_DATA_POINTS = 1200  # Максимальна кількість точок даних для графіків (2 хвилини при інтервалі 0.1с)
UPDATE_INTERVAL = 0.1   # Інтервал оновлення метрик у секундах (10 разів/сек)
AUDIO_UPDATE_INTERVAL = 1.0 # Інтервал оновлення аудіо даних у секундах (1 раз/сек)

# Ініціалізація Flask-додатку
app = Flask(__name__)

# Сховище для даних моніторингу
data_points = {
    "timestamps": deque(maxlen=MAX_DATA_POINTS), # Часові мітки
    "cpu_temp": deque(maxlen=MAX_DATA_POINTS),   # Температура CPU
    "cpu_load": deque(maxlen=MAX_DATA_POINTS),   # Навантаження CPU
    "gpu_temp": deque(maxlen=MAX_DATA_POINTS),   # Температура GPU
    "gpu_load": deque(maxlen=MAX_DATA_POINTS),   # Навантаження GPU
    "cpu_freq": deque(maxlen=MAX_DATA_POINTS),   # Частота CPU
}
latest_ram_usage = 0 # Змінна для зберігання останнього значення використання RAM

# Global variable for selected sink index (no longer a global Pulse object)
selected_sink_index = None

# --- Helper function to get a PulseAudio client instance ---
def get_pulse_client():
    """
    Створює та повертає новий екземпляр клієнта PulseAudio.
    Повертає None, якщо pulsectl недоступний або є помилка підключення.
    """
    if not pulsectl:
        return None
    try:
        # 'with' statement ensures the client is automatically closed
        # You'll need to use 'with get_pulse_client() as p:' wherever you need it
        return pulsectl.Pulse('system-monitor-client')
    except pulsectl.PulseError as e:
        print(f"Помилка підключення до PulseAudio: {e}. Операції з аудіо можуть бути вимкнені.")
        return None


def get_cpu_temperature():
    """
    Отримує поточну температуру CPU.
    Шукає датчики 'k10temp' (для AMD) або 'coretemp' (для Intel).
    Повертає 0, якщо дані недоступні.
    """
    try:
        temps = psutil.sensors_temperatures()
        if 'k10temp' in temps: # Зазвичай для процесорів AMD
            for entry in temps['k10temp']:
                return entry.current
        if 'coretemp' in temps: # Зазвичай для процесорів Intel
            return temps['coretemp'][0].current
    except (AttributeError, KeyError):
        return 0 # Повертаємо 0, якщо не вдалося отримати температуру
    return 0

def get_gpu_metrics():
    """
    Отримує температуру та навантаження GPU NVIDIA за допомогою команди nvidia-smi.
    Повертає (0, 0), якщо nvidia-smi не знайдено або сталася помилка.
    """
    try:
        # Запит лише необхідних даних у форматі CSV без заголовків та одиниць
        command = "nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader,nounits"
        # stderr=subprocess.PIPE дозволяє захоплювати помилки nvidia-smi
        result = subprocess.check_output(command, shell=True, text=True, stderr=subprocess.PIPE)
        temp_str, load_str = result.strip().split(', ')
        return int(temp_str), int(load_str)
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return 0, 0 # Повертаємо (0,0) у випадку помилки

def get_sinks():
    """
    Отримує список доступних аудіо-виходів (sink'ів) для PulseAudio.
    Повертає список словників з індексом, назвою та статусом "за замовчуванням".
    """
    with get_pulse_client() as p:
        if not p:
            return []
        sinks_data = []
        try:
            # Отримуємо інформацію про сервер, щоб знайти пристрій за замовчуванням
            server_info = p.server_info()
            for sink in p.sink_list():
                sinks_data.append({
                    "index": sink.index,
                    "name": sink.description, # Зручна для користувача назва пристрою
                    "is_default": sink.name == server_info.default_sink_name # Перевіряємо, чи це пристрій за замовчуванням
                })
            return sinks_data
        except Exception as e:
            print(f"Помилка отримання списку пристроїв виводу (sinks): {e}")
            return []

def get_current_sink_volume(sink_index=None):
    """
    Отримує гучність та стан вимкнення звуку для вказаного або вибраного sink'а.
    Якщо sink_index не вказано, намагається використовувати selected_sink_index,
    потім повертається до пристрою за замовчуванням.
    """
    with get_pulse_client() as p:
        if not p:
            return 0, False, None # Гучність, Вимкнено, Фактичний_Індекс_Sink

        target_sink = None
        # Шукаємо sink за наданим індексом
        if sink_index is not None:
            for s in p.sink_list():
                if s.index == sink_index:
                    target_sink = s
                    break
        
        # Якщо за наданим індексом не знайдено, шукаємо за глобально вибраним індексом
        if target_sink is None and selected_sink_index is not None:
            for s in p.sink_list():
                if s.index == selected_sink_index:
                    target_sink = s
                    break

        # Якщо все ще не знайдено, використовуємо пристрій за замовчуванням
        if target_sink is None:
            try:
                target_sink = p.get_default_sink()
            except pulsectl.PulseError:
                target_sink = None # Може бути, що немає доступних пристроїв

        if target_sink:
            volume = int(target_sink.volume.value_flat * 100) # Конвертуємо в 0-100%
            muted = target_sink.mute
            return volume, muted, target_sink.index
        return 0, False, None # Якщо sink не знайдено

def set_sink_volume(sink_index, volume):
    """
    Встановлює гучність для вказаного sink'а (для PulseAudio).
    Гучність встановлюється в діапазоні від 0.0 до 1.0.
    Якщо гучність > 0 і пристрій був вимкнений, він вмикається.
    """
    with get_pulse_client() as p:
        if not p:
            return
        try:
            for sink in p.sink_list():
                if sink.index == sink_index:
                    p.volume_set_all_chans(sink, volume / 100.0)
                    if volume > 0 and sink.mute: # Якщо гучність > 0 і було вимкнено, увімкнути
                        p.mute(sink, False)
                    break
        except Exception as e:
            print(f"Помилка встановлення гучності пристрою виводу (sink): {e}")

def toggle_sink_mute(sink_index, mute_state):
    """
    Перемикає вимкнення звуку для вказаного sink'а (для PulseAudio).
    """
    with get_pulse_client() as p:
        if not p:
            return
        try:
            for sink in p.sink_list():
                if sink.index == sink_index:
                    p.mute(sink, mute_state)
                    break
        except Exception as e:
            print(f"Помилка перемикання вимкнення звуку пристрою виводу (sink): {e}")


def get_application_volumes():
    """
    Отримує гучність запущених програм (sink inputs) для PulseAudio.
    Повертає список словників з індексом, назвою, гучністю та станом вимкнення звуку.
    """
    with get_pulse_client() as p:
        if not p:
            return []
        app_volumes = []
        try:
            for sink_input in p.sink_input_list():
                # Отримуємо назву програми, або використовуємо назву медіа, або загальну назву
                name = sink_input.proplist.get('application.name',
                                               sink_input.proplist.get('media.name',
                                                                       f"Невідома програма {sink_input.index}"))
                volume = int(sink_input.volume.value_flat * 100) # Конвертуємо в 0-100%
                muted = sink_input.mute
                app_volumes.append({
                    "index": sink_input.index,
                    "name": name,
                    "volume": volume,
                    "muted": muted
                })
        except Exception as e:
            print(f"Помилка отримання гучності програм: {e}")
        return app_volumes

def set_application_volume(index, volume):
    """
    Встановлює гучність для конкретної програми за її індексом (для PulseAudio).
    """
    with get_pulse_client() as p:
        if not p:
            return
        try:
            for sink_input in p.sink_input_list():
                if sink_input.index == index:
                    p.volume_set_all_chans(sink_input, volume / 100.0)
                    if volume > 0 and sink_input.mute: # Якщо гучність > 0 і було вимкнено, увімкнути
                        p.mute(sink_input, False)
                    break
        except Exception as e:
            print(f"Помилка встановлення гучності програми: {e}")

def toggle_application_mute(index, mute_state):
    """
    Перемикає вимкнення звуку для конкретної програми за її індексом (для PulseAudio).
    """
    with get_pulse_client() as p:
        if not p:
            return
        try:
            for sink_input in p.sink_input_list():
                if sink_input.index == index:
                    p.mute(sink_input, mute_state)
                    break
        except Exception as e:
            print(f"Помилка перемикання вимкнення звуку програми: {e}")


def collect_metrics():
    """
    Функція, що безперервно збирає метрики системи у фоновому потоці.
    """
    global latest_ram_usage
    while True:
        # Збір даних CPU, GPU, RAM
        cpu_load = psutil.cpu_percent(interval=None)
        cpu_temp = get_cpu_temperature()
        gpu_temp, gpu_load = get_gpu_metrics()
        ram_usage = psutil.virtual_memory().percent
        
        cpu_freq_current = 0
        if psutil.cpu_freq() is not None:
            cpu_freq_current = psutil.cpu_freq().current # Отримуємо поточну частоту в МГц

        # Додавання зібраних даних до сховища
        timestamp = datetime.now().strftime("%H:%M:%S")
        data_points["timestamps"].append(timestamp)
        data_points["cpu_temp"].append(cpu_temp)
        data_points["cpu_load"].append(cpu_load)
        data_points["gpu_temp"].append(gpu_temp)
        data_points["gpu_load"].append(gpu_load)
        data_points["cpu_freq"].append(cpu_freq_current)

        latest_ram_usage = ram_usage # Оновлюємо останнє значення RAM
        
        time.sleep(UPDATE_INTERVAL) # Чекаємо перед наступним збором

# --- Flask-маршрути ---

@app.route('/')
def index():
    """Головна сторінка, що віддає веб-інтерфейс."""
    return render_template('index.html')

@app.route('/data')
def get_data():
    """Endpoint для передачі зібраних даних метрик у форматі JSON."""
    # Перетворення deque в списки для серіалізації в JSON
    response_data = {key: list(value) for key, value in data_points.items()}
    response_data["ram_usage"] = latest_ram_usage
    return jsonify(response_data)

@app.route('/audio_data')
def get_audio_data():
    """
    Endpoint для отримання даних про гучність, включаючи список пристроїв виводу.
    Оновлює глобальний selected_sink_index.
    """
    global selected_sink_index

    sinks = get_sinks() # Отримуємо список доступних пристроїв виводу
    
    # Якщо жоден sink не вибрано, намагаємося вибрати пристрій за замовчуванням
    if selected_sink_index is None and sinks:
        for sink in sinks:
            if sink["is_default"]:
                selected_sink_index = sink["index"]
                break
        if selected_sink_index is None: # Якщо пристрій за замовчуванням не знайдено, вибираємо перший
            selected_sink_index = sinks[0]["index"]

    # Отримуємо гучність та стан вимкнення звуку для поточного вибраного sink
    master_vol, master_muted, actual_selected_sink_index = get_current_sink_volume(selected_sink_index)
    
    # Оновлюємо selected_sink_index, якщо попередньо вибраний зник або змінився пристрій за замовчуванням
    if actual_selected_sink_index is not None and actual_selected_sink_index != selected_sink_index:
        selected_sink_index = actual_selected_sink_index

    app_vols = get_application_volumes() # Отримуємо гучність програм
    return jsonify({
        "sinks": sinks,                     # Список доступних пристроїв виводу
        "selected_sink_index": selected_sink_index, # Індекс поточного вибраного пристрою
        "master_volume": master_vol,        # Загальна гучність вибраного пристрою
        "master_muted": master_muted,       # Стан вимкнення звуку вибраного пристрою
        "application_volumes": app_vols      # Список гучності програм
    })

@app.route('/set_selected_sink/<int:sink_index>', methods=['POST'])
def set_selected_sink_route(sink_index):
    """
    Endpoint для встановлення вибраного пристрою виводу.
    Оновлює глобальний selected_sink_index.
    """
    global selected_sink_index
    selected_sink_index = sink_index
    return jsonify({"status": "success"})


@app.route('/set_master_volume/<int:volume>', methods=['POST'])
def set_master_volume_route(volume):
    """
    Endpoint для встановлення загальної гучності вибраного пристрою.
    """
    if selected_sink_index is not None:
        set_sink_volume(selected_sink_index, volume)
    return jsonify({"status": "success"})

@app.route('/toggle_master_mute/<int:mute_state>', methods=['POST'])
def toggle_master_mute_route(mute_state):
    """
    Endpoint для перемикання вимкнення загального звуку вибраного пристрою.
    """
    if selected_sink_index is not None:
        toggle_sink_mute(selected_sink_index, bool(mute_state))
    return jsonify({"status": "success"})


@app.route('/set_app_volume/<int:index>/<int:volume>', methods=['POST'])
def set_app_volume_route(index, volume):
    """
    Endpoint для встановлення гучності програми за її індексом.
    """
    set_application_volume(index, volume)
    return jsonify({"status": "success"})

@app.route('/toggle_app_mute/<int:index>/<int:mute_state>', methods=['POST'])
def toggle_app_mute_route(index, mute_state):
    """
    Endpoint для перемикання вимкнення звуку програми за її індексом.
    """
    toggle_application_mute(index, bool(mute_state))
    return jsonify({"status": "success"})


if __name__ == '__main__':
    # Запуск збору метрик у фоновому потоці
    metrics_thread = threading.Thread(target=collect_metrics, daemon=True)
    metrics_thread.start()
    
    # Запуск веб-сервера Flask
    # host='0.0.0.0' робить сервер доступним у локальній мережі
    # debug=True дозволяє автоматичне перезавантаження сервера при змінах коду та надає більше інформації про помилки
    app.run(host='0.0.0.0', port=5000, debug=True)