"""設定管理 - .env からAPIキー読み込み、プロバイダ切替"""

import os
from pathlib import Path
from dotenv import load_dotenv

# プロジェクトルートの .env を読み込む
_project_root = Path(__file__).parent.parent
load_dotenv(_project_root / ".env")

# LLM プロバイダ設定
PROVIDER = os.getenv("LLM_PROVIDER", "cerebras")

_PROVIDERS = {
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_env": "CEREBRAS_API_KEY",
        "model": "gpt-oss-120b",
    },
    "vllm": {
        "base_url": os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1"),
        "api_key_env": "VLLM_API_KEY",
        "model": os.getenv("VLLM_MODEL", "gpt-oss-120b"),
    },
}


def get_llm_config() -> dict:
    """現在のプロバイダのLLM設定を返す"""
    provider = _PROVIDERS.get(PROVIDER)
    if provider is None:
        raise ValueError(f"Unknown provider: {PROVIDER}. Use: {list(_PROVIDERS.keys())}")

    api_key = os.getenv(provider["api_key_env"], "dummy")
    return {
        "base_url": os.getenv("LLM_BASE_URL", provider["base_url"]),
        "api_key": api_key,
        "model": os.getenv("LLM_MODEL", provider["model"]),
    }


# エージェント設定
MAX_TURNS = int(os.getenv("MAX_TURNS", "20"))
MAX_OUTPUT_CHARS = int(os.getenv("MAX_OUTPUT_CHARS", "10000"))

# 作業ディレクトリ
WORKING_DIR = os.getenv("WORKING_DIR", os.getcwd())
