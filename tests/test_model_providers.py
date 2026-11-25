import os
import unittest
from unittest.mock import patch

import httpx

from queryatlas import dashscope, llm


class ModelProvidersTest(unittest.TestCase):
    @patch.dict(
        os.environ, {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "test-only"}
    )
    @patch("queryatlas.llm.httpx.post")
    def test_deepseek_flash_request(self, post):
        post.return_value = httpx.Response(
            200,
            json={"choices": [{"message": {"content": " 答案 "}}]},
            request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
        )
        self.assertEqual(llm.complete("系统", "用户", limit=32), "答案")
        self.assertEqual(
            post.call_args.args[0], "https://api.deepseek.com/chat/completions"
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "deepseek-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["max_tokens"], 32)

    @patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-only"})
    @patch("queryatlas.dashscope.httpx.post")
    def test_dashscope_embedding_order_and_shape(self, post):
        def reply(vectors):
            post.return_value = httpx.Response(
                200,
                json={
                    "output": {
                        "embeddings": [
                            {"text_index": index, "embedding": vector}
                            for index, vector in reversed(list(enumerate(vectors)))
                        ]
                    }
                },
                request=httpx.Request("POST", dashscope.URL),
            )

        first, second = [1.0] * 512, [2.0] * 512
        reply([first, second])
        self.assertEqual(dashscope.encode(["一", "二"], "document"), [first, second])
        self.assertEqual(
            post.call_args.kwargs["json"]["parameters"],
            {
                "text_type": "document",
                "dimension": 512,
            },
        )

        reply([[1.0]])
        with self.assertRaises(dashscope.EmbeddingUnavailable):
            dashscope.encode(["问题"], "query")
        self.assertEqual(
            post.call_args.kwargs["json"]["parameters"]["text_type"], "query"
        )

    def test_dashscope_routes_only_to_alibaba_endpoints(self):
        with patch.dict(os.environ, {"DASHSCOPE_WORKSPACE_ID": ""}):
            self.assertEqual(dashscope.endpoint(), dashscope.URL)
        with patch.dict(os.environ, {"DASHSCOPE_WORKSPACE_ID": "llm-example"}):
            self.assertEqual(
                dashscope.endpoint(),
                "https://llm-example.cn-beijing.maas.aliyuncs.com"
                + dashscope.EMBEDDING_PATH,
            )
        with patch.dict(os.environ, {"DASHSCOPE_WORKSPACE_ID": "example.com/?"}):
            with self.assertRaises(dashscope.EmbeddingUnavailable):
                dashscope.endpoint()
