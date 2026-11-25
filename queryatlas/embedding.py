import os
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
from llama_index.core.schema import TextNode
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.ingestion import IngestionPipeline
from tokenizers import Tokenizer

from . import dashscope

# Pinned CPU ONNX export of BAAI/bge-small-zh-v1.5 (CLS, L2-normalized).
LOCAL_MODEL = "Xenova/bge-small-zh-v1.5@75c43b06"
CLOUD_MODEL = "dashscope/" + dashscope.MODEL
MODEL = CLOUD_MODEL if os.getenv("EMBEDDING_PROVIDER") == "dashscope" else LOCAL_MODEL
MODEL_DIR = Path(os.getenv("MODEL_DIR", "/models"))
MODEL_LOCK = threading.Lock()


class Encoder:
    def __init__(self):
        self.tokenizer = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
        self.tokenizer.enable_truncation(512)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 2
        self.session = ort.InferenceSession(
            str(MODEL_DIR / "model_quantized.onnx"),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )

    def encode(self, texts):
        with MODEL_LOCK:
            tokens = self.tokenizer.encode_batch(texts)
            values = {
                "input_ids": [t.ids for t in tokens],
                "attention_mask": [t.attention_mask for t in tokens],
                "token_type_ids": [t.type_ids for t in tokens],
            }
            inputs = {
                i.name: np.array(values[i.name], dtype=np.int64)
                for i in self.session.get_inputs()
            }
            vectors = self.session.run(None, inputs)[0][:, 0, :]
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-9)
            return vectors.tolist()


@lru_cache(maxsize=1)
def encoder():
    return Encoder()


class LocalEmbedding(BaseEmbedding):
    model_name: str = LOCAL_MODEL

    def _get_text_embedding(self, text):
        return encoder().encode([text])[0]

    def _get_text_embeddings(self, texts):
        return encoder().encode(texts)

    def _get_query_embedding(self, query):
        return self._get_text_embedding(
            "为这个句子生成表示以用于检索相关文章：" + query
        )

    async def _aget_query_embedding(self, query):
        from asyncio import to_thread

        return await to_thread(self._get_query_embedding, query)


class DashScopeEmbedding(BaseEmbedding):
    model_name: str = CLOUD_MODEL
    embed_batch_size: int = dashscope.MAX_BATCH

    def _get_text_embedding(self, text):
        return self._get_text_embeddings([text])[0]

    def _get_text_embeddings(self, texts):
        return dashscope.encode(texts, "document")

    def _get_query_embedding(self, query):
        return dashscope.encode([query], "query")[0]

    async def _aget_query_embedding(self, query):
        from asyncio import to_thread

        return await to_thread(self._get_query_embedding, query)


def active_embedding():
    return (
        DashScopeEmbedding()
        if MODEL == CLOUD_MODEL
        else LocalEmbedding(embed_batch_size=16)
    )


def prepare_embedding():
    if MODEL == CLOUD_MODEL:
        dashscope.credentials()
        dashscope.endpoint()
    else:
        encoder()


def index_nodes(passages):
    nodes = []
    for p in passages:
        for start in range(0, len(p.text), 340):
            node = TextNode(
                text=p.text[start : start + 400],
                metadata={"anchor": p.anchor, "method": p.method, "model": MODEL},
                excluded_embed_metadata_keys=["anchor", "method", "model"],
            )
            nodes.append(node)
            if start + 400 >= len(p.text):
                break
    pipeline = IngestionPipeline(
        transformations=[active_embedding()], disable_cache=True
    )
    return pipeline.run(nodes=nodes, show_progress=False)
