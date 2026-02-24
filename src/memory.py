"""記憶管理 - セッション間で永続化する重要情報"""

from pathlib import Path

MEMORY_DIR = Path(__file__).parent.parent / "agent_memory"
MEMORY_FILE = MEMORY_DIR / "MEMORY.md"


def _ensure_dir():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def load_memory() -> str:
    """記憶ファイルを読み込む。なければ空文字を返す"""
    if MEMORY_FILE.exists():
        text = MEMORY_FILE.read_text(encoding="utf-8")
        # 先頭200行に制限
        lines = text.splitlines()
        if len(lines) > 200:
            return "\n".join(lines[:200]) + "\n... (truncated)"
        return text
    return ""


def save_memory(content: str):
    """記憶ファイルに書き込む"""
    _ensure_dir()
    MEMORY_FILE.write_text(content, encoding="utf-8")


def append_memory(entry: str):
    """記憶ファイルに追記する"""
    _ensure_dir()
    current = ""
    if MEMORY_FILE.exists():
        current = MEMORY_FILE.read_text(encoding="utf-8")
    if current and not current.endswith("\n"):
        current += "\n"
    current += entry + "\n"
    MEMORY_FILE.write_text(current, encoding="utf-8")


def load_topic_memory(topic: str) -> str:
    """トピック別の記憶ファイルを読み込む"""
    path = MEMORY_DIR / f"{topic}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def save_topic_memory(topic: str, content: str):
    """トピック別の記憶ファイルに書き込む"""
    _ensure_dir()
    path = MEMORY_DIR / f"{topic}.md"
    path.write_text(content, encoding="utf-8")
