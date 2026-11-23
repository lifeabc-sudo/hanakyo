"""ComfyUI API で SD1.5 画像を1枚生成するサンプル。
使い方: python generate.py "a cat on a sofa" [steps]
"""
import json, sys, time, urllib.request, random

URL = "http://127.0.0.1:8188"
prompt = sys.argv[1] if len(sys.argv) > 1 else "a beautiful landscape, mountains, sunset"
steps = int(sys.argv[2]) if len(sys.argv) > 2 else 20

wf = {
  "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5-pruned-emaonly-fp16.safetensors"}},
  "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}},
  "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
  "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry, low quality, watermark", "clip": ["4", 1]}},
  "3": {"class_type": "KSampler", "inputs": {"seed": random.randint(0, 2**32), "steps": steps, "cfg": 7.0,
        "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
        "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
  "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
  "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "hanakyo", "images": ["8", 0]}},
}

req = urllib.request.Request(URL + "/prompt", data=json.dumps({"prompt": wf}).encode(),
                             headers={"Content-Type": "application/json"})
pid = json.load(urllib.request.urlopen(req))["prompt_id"]
print("queued", pid)
while True:
    h = json.load(urllib.request.urlopen(f"{URL}/history/{pid}"))
    if pid in h:
        for img in h[pid]["outputs"]["9"]["images"]:
            print("saved: ComfyUI/output/" + img["filename"])
        break
    time.sleep(2)
