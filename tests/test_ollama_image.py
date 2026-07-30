import base64
import requests

IMAGE_PATH = r"C:\EnnoSmart\data\raw_documents\Archi_V1.drawio (2).png"

with open(IMAGE_PATH, "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

payload = {
    "model": "llama3.2-vision",
    "prompt": "Describe this image in a technical way.",
    "images": [img_b64],
    "stream": False
}

res = requests.post(
    "http://localhost:11434/api/generate",
    json=payload,
    timeout=300
)

print(res.json()["response"])