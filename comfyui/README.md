# ComfyUI セットアップ

- `./setup_comfyui.sh` … ComfyUI(CPU版)をインストールし、SD1.5モデルを取得してサーバーを起動（ポート 8188）
- `python3 generate.py "プロンプト(英語)" [steps]` … API経由で 512x512 画像を生成。出力は `/home/user/ComfyUI/output/`
- CPUのみの環境なので 20ステップで約2分かかります
