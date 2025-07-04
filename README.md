System Monitor & Audio Controller

📊 Realtime System Monitoring & Audio Control Dashboard with:

CPU/GPU temperature and load charts

RAM usage

CPU frequency chart

Audio Mixer:

Master volume control for selected output device

Dynamic list of active application volumes

Ability to switch playback device for individual applications

![image](https://github.com/user-attachments/assets/e00e8d76-ccb3-4272-b542-344b10866440)

📦 Requirements

Python 3.8+

Flask

psutil

pulsectl (for Linux audio control)

nvidia-smi (for NVIDIA GPU metrics)

🚀 Run

Bash

pip install -r requirements.txt
python app.py
