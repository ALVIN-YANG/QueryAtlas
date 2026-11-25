"""Chat completion calls for the local model or the official DeepSeek API."""

import os

import httpx
from fastapi import HTTPException

DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_URL = "https://api.deepseek.com"


def uses_deepseek():
    return os.getenv("LLM_PROVIDER", "local") == "deepseek"


def deepseek_headers():
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise ValueError("缺少 DeepSeek API 密钥")
    return {"Authorization": f"Bearer {key}"}


def complete(system, user, limit=260):
    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": limit,
        "temperature": 0,
    }
    provider = "DeepSeek Flash" if uses_deepseek() else "本地问答模型"
    try:
        if uses_deepseek():
            url = f"{DEEPSEEK_URL}/chat/completions"
            headers = deepseek_headers()
            payload.update(model=DEEPSEEK_MODEL, thinking={"type": "disabled"})
        else:
            url = os.getenv("LLM_URL", "http://llm:8080/v1/chat/completions")
            headers = {}
        response = httpx.post(
            url,
            headers=headers,
            json=payload,
            timeout=httpx.Timeout(65, connect=5),
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty completion")
        return content.strip()
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
        raise HTTPException(503, f"{provider}暂不可用，请重试") from error
