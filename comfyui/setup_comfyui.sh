#!/usr/bin/env bash
# ComfyUI (CPU版) のインストール + SD1.5 モデル取得 + 起動
set -e
DIR=${COMFY_DIR:-/home/user/ComfyUI}
[ -d "$DIR" ] || git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$DIR"
cd "$DIR"
[ -d venv ] || python3 -m venv venv
. venv/bin/activate
python -c "import torch" 2>/dev/null || pip install -q torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -q -r requirements.txt
CK=models/checkpoints/v1-5-pruned-emaonly-fp16.safetensors
[ -s "$CK" ] || curl -fsSL -o "$CK" https://huggingface.co/Comfy-Org/stable-diffusion-1.5/resolve/main/v1-5-pruned-emaonly-fp16.safetensors
curl -s localhost:8188/system_stats >/dev/null || nohup python main.py --cpu --listen 127.0.0.1 --port 8188 > comfyui.log 2>&1 &
echo "ComfyUI: http://127.0.0.1:8188"
