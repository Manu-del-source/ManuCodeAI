import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("AI_API_KEY")
base_url = os.getenv("AI_BASE_URL", "").rstrip("/")

if not api_key or not base_url:
    print("Missing API key or base URL in .env")
    raise SystemExit(1)

url = f"{base_url}/models"

print(f"Checking: {url}")

try:
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        },
        timeout=30
    )

    response.raise_for_status()
    data = response.json()

    print("\nAVAILABLE MODELS")
    print("=" * 55)

    models = data.get("data", [])

    for model in models:
        model_id = model.get("id", "Unknown")
        print(model_id)

    print("=" * 55)
    print(f"Total models returned: {len(models)}")

except requests.exceptions.HTTPError:
    print("API error:", response.status_code)
    print(response.text[:2000])

except Exception as error:
    print("Error:", error)
