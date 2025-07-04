# System Monitor with Charts and htop-style Process Viewer

📊 Realtime system monitoring dashboard with:
- CPU/GPU temperature and load charts
- RAM usage
- Process list with CPU/RAM usage (like `htop`)
- Kill process button

## 📦 Requirements

- Python 3.8+
- Flask
- psutil
- `nvidia-smi` (for NVIDIA GPU metrics)

## 🚀 Run

```bash
pip install -r requirements.txt
python app.py
