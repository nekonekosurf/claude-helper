"""コンテキスト圧縮 - トークン数推定と古い会話の要約"""

from src.llm_client import chat


def estimate_tokens(text: str) -> int:
    """トークン数を概算（日本語: 1文字≒1.5トークン、英語: 1単語≒1.3トークン）"""
    jp_chars = sum(1 for c in text if ord(c) > 127)
    en_chars = len(text) - jp_chars
    return int(jp_chars * 1.5 + en_chars * 0.3)


def estimate_messages_tokens(messages: list) -> int:
    """メッセージリスト全体のトークン数を概算"""
    total = 0
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
        else:
            content = getattr(msg, "content", "") or ""
        if content:
            total += estimate_tokens(str(content))
        # ツール呼び出しの分も加算
        tool_calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                if isinstance(tc, dict):
                    total += estimate_tokens(tc.get("function", {}).get("arguments", ""))
                else:
                    total += estimate_tokens(tc.function.arguments or "")
    return total


def compress_context(client, model: str, messages: list, max_tokens: int = 30000) -> list:
    """コンテキストが大きすぎる場合、古い会話を要約して圧縮する

    保持するもの:
    - システムプロンプト（messages[0]）
    - 直近5ターン
    圧縮するもの:
    - それ以前の会話を要約
    """
    current_tokens = estimate_messages_tokens(messages)
    threshold = int(max_tokens * 0.7)

    if current_tokens < threshold:
        return messages  # 圧縮不要

    # システムプロンプトを保持
    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None

    # 直近のメッセージを保持（user/assistant ペアで5ターン分）
    keep_count = min(10, len(messages) - 1)
    recent = messages[-keep_count:]
    old = messages[1:-keep_count] if len(messages) > keep_count + 1 else []

    if not old:
        return messages  # 圧縮するものがない

    # 古い会話を要約
    summary_content = _build_summary_text(old)
    summary_request = (
        "以下の会話の要約を作成してください。重要な情報（ファイルパス、関数名、"
        "エラーメッセージ、決定事項）を保持し、200文字以内にまとめてください:\n\n"
        + summary_content
    )

    try:
        summary_response = chat(
            client, model,
            [{"role": "user", "content": summary_request}],
            tools=None,
        )
        summary_text = summary_response.content or summary_content[:500]
    except Exception:
        # LLM呼び出し失敗時は単純切り詰め
        summary_text = summary_content[:500]

    # 圧縮後のメッセージリストを組み立て
    compressed = []
    if system_msg:
        compressed.append(system_msg)
    compressed.append({
        "role": "user",
        "content": f"[前回の会話の要約]\n{summary_text}",
    })
    compressed.append({
        "role": "assistant",
        "content": "承知しました。前回の内容を踏まえて対応します。",
    })
    compressed.extend(recent)

    return compressed


def _build_summary_text(messages: list) -> str:
    """メッセージリストをテキスト形式にまとめる"""
    parts = []
    for msg in messages:
        role = msg.get("role", "?") if isinstance(msg, dict) else getattr(msg, "role", "?")
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        if content:
            parts.append(f"[{role}] {str(content)[:300]}")
    return "\n".join(parts)
