"""Alibaba Cloud Model Studio text embeddings, using its native HTTP endpoint."""

import math
import os
import re

import httpx

DIMENSION = 512
MAX_BATCH = 10
MODEL = "text-embedding-v4"
EMBEDDING_PATH = "/api/v1/services/embeddings/text-embedding/text-embedding"
URL = "https://dashscope.aliyuncs.com" + EMBEDDING_PATH


class EmbeddingUnavailable(RuntimeError):
    pass


def credentials():
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise EmbeddingUnavailable("缺少阿里云百炼 API 密钥")
    return {"Authorization": f"Bearer {key}"}


def endpoint():
    workspace = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
    if not workspace:
        return URL
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", workspace):
        raise EmbeddingUnavailable("阿里云百炼业务空间 ID 格式无效")
    return f"https://{workspace}.cn-beijing.maas.aliyuncs.com{EMBEDDING_PATH}"


def encode(texts, kind):
    try:
        response = httpx.post(
            endpoint(),
            headers=credentials(),
            json={
                "model": MODEL,
                "input": {"texts": texts},
                "parameters": {"text_type": kind, "dimension": DIMENSION},
            },
            timeout=httpx.Timeout(25, connect=5),
        )
        response.raise_for_status()
        items = sorted(
            response.json()["output"]["embeddings"], key=lambda item: item["text_index"]
        )
        if [item["text_index"] for item in items] != list(range(len(texts))):
            raise ValueError("invalid text indices")
        values = [item["embedding"] for item in items]
        if any(
            len(vector) != DIMENSION
            or not all(
                isinstance(value, (int, float)) and math.isfinite(value)
                for value in vector
            )
            or not any(vector)
            for vector in values
        ):
            raise ValueError("invalid embedding")
        return values
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise EmbeddingUnavailable(
            "阿里云百炼向量服务暂不可用，请检查凭据和额度"
        ) from error
