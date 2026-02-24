"""LLM接続クライアント - OpenAI互換API（Cerebras / vLLM 共通）"""

from openai import OpenAI
from src.config import get_llm_config


def create_client() -> tuple[OpenAI, str]:
    """OpenAIクライアントとモデル名を返す"""
    cfg = get_llm_config()
    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
    return client, cfg["model"]


def chat(client: OpenAI, model: str, messages: list, tools: list | None = None) -> object:
    """LLMにリクエストを送信してレスポンスを返す"""
    kwargs = {
        "model": model,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message
