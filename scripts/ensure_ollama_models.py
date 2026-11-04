#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ensures required Ollama models (generation and embeddings) are available (OPS-01).
Calls Ollama /api/tags to inspect available models, and /api/pull if a model is missing.
"""
import os
import sys
import json
import httpx
from core.config import settings


def ensure_models(ollama_base_url: str = None, timeout: float = 300.0) -> bool:
    base_url = ollama_base_url or settings.OLLAMA_BASE_URL
    required_models = [settings.OLLAMA_MODEL, settings.OLLAMA_EMBED_MODEL]

    print(f"[OLLAMA-INIT] Checking Ollama service at {base_url}...")
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(f"{base_url}/api/tags")
            if resp.status_code != 200:
                print(f"[OLLAMA-INIT] Error: Ollama /api/tags returned status {resp.status_code}")
                return False

            data = resp.json()
            available = [m.get("name", "") for m in data.get("models", [])]
            print(f"[OLLAMA-INIT] Available models: {available}")

            for model in required_models:
                # Check if model or model with :latest tag is present
                model_base = model.split(":")[0]
                is_present = any(
                    m == model or m.startswith(f"{model_base}:")
                    for m in available
                )
                if is_present:
                    print(f"[OLLAMA-INIT] Model '{model}' is present.")
                else:
                    print(f"[OLLAMA-INIT] Model '{model}' missing; initiating pull...")
                    pull_resp = client.post(
                        f"{base_url}/api/pull",
                        json={"name": model, "stream": False},
                        timeout=timeout,
                    )
                    if pull_resp.status_code == 200:
                        print(f"[OLLAMA-INIT] Model '{model}' pulled successfully.")
                    else:
                        print(f"[OLLAMA-INIT] WARNING: Failed to pull '{model}': {pull_resp.text}")
                        return False

        print("[OLLAMA-INIT] All required models verified.")
        return True
    except Exception as e:
        print(f"[OLLAMA-INIT] Error communicating with Ollama: {e}")
        return False


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else None
    success = ensure_models(url)
    sys.exit(0 if success else 1)
