"""セッション管理 - 会話履歴の保存/復元"""

import json
import time
from pathlib import Path
from datetime import datetime

SESSIONS_DIR = Path(__file__).parent.parent / "sessions"


def _ensure_dir():
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def generate_session_id() -> str:
    """セッションIDを生成（タイムスタンプベース）"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_session(session_id: str, messages: list, metadata: dict | None = None):
    """セッションを保存（JSONL形式）"""
    _ensure_dir()
    path = SESSIONS_DIR / f"{session_id}.jsonl"

    with open(path, "w", encoding="utf-8") as f:
        # メタデータ行
        meta = {
            "_type": "metadata",
            "session_id": session_id,
            "saved_at": datetime.now().isoformat(),
            "message_count": len(messages),
            **(metadata or {}),
        }
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")

        # メッセージ行
        for msg in messages:
            row = {"_type": "message"}
            if isinstance(msg, dict):
                row.update(msg)
            else:
                # OpenAI message object
                row.update(msg.model_dump() if hasattr(msg, 'model_dump') else dict(msg))
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_session(session_id: str) -> tuple[list, dict]:
    """セッションを復元。(messages, metadata) を返す"""
    path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"セッションが見つかりません: {session_id}")

    messages = []
    metadata = {}

    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("_type") == "metadata":
                metadata = {k: v for k, v in row.items() if k != "_type"}
            elif row.get("_type") == "message":
                msg = {k: v for k, v in row.items() if k != "_type"}
                messages.append(msg)

    return messages, metadata


def list_sessions(limit: int = 10) -> list[dict]:
    """最近のセッション一覧を返す"""
    _ensure_dir()
    files = sorted(SESSIONS_DIR.glob("*.jsonl"), reverse=True)

    sessions = []
    for path in files[:limit]:
        try:
            with open(path, encoding="utf-8") as f:
                first_line = f.readline().strip()
                if first_line:
                    meta = json.loads(first_line)
                    if meta.get("_type") == "metadata":
                        sessions.append({
                            "session_id": meta.get("session_id", path.stem),
                            "saved_at": meta.get("saved_at", ""),
                            "message_count": meta.get("message_count", 0),
                        })
        except Exception:
            continue

    return sessions


def get_latest_session_id() -> str | None:
    """最新のセッションIDを返す"""
    sessions = list_sessions(limit=1)
    return sessions[0]["session_id"] if sessions else None
