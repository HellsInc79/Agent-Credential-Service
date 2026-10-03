# ollama_client.py

import requests

OLLAMA_URL = (
    "http://127.0.0.1:11434/api/generate"
)


def ask_model(
        model,
        prompt,
        system
):

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": False
        },
        timeout=600
    )

    response.raise_for_status()

    return response.json()


def list_models():

    response = requests.get(
        "http://127.0.0.1:11434/api/tags",
        timeout=30
    )

    response.raise_for_status()

    return response.json()