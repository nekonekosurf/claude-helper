"""
セッション管理 (拡張版)

既存の session.py を補完する高機能版:
- セッションのメタデータ管理
- 検索インデックス（セッション内容でフィルタ）
- セッションのエクスポート（Markdown形式）
- 自動アーカイブ（古いセッションを圧縮）
- セッション間でのコンテキスト引き継ぎ
"""

from __future__ import annotations

import json
import gzip
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


SESSION_DIR   = Path(__file__).parent.parent / "sessions"
ARCHIVE_DIR   = SESSION_DIR / "archive"
INDEX_FILE    = SESSION_DIR / "session_index.json"
MAX_SESSIONS  = 50     # インデックス上限（超えたら古いものをアーカイブ）
ARCHIVE_DAYS  = 7      # 7日以上経過したセッションをアーカイブ


# ---------------------------------------------------------------------------
# データ型
# ---------------------------------------------------------------------------

@dataclass
class SessionMeta:
    """セッションメタデータ"""
    session_id: str
    created_at: float
    updated_at: float
    message_count: int
    title: str = ""           # 最初のユーザーメッセージから自動生成
    tags: list[str] = field(default_factory=list)
    archived: bool = False
    model: str = ""
    total_tokens: int = 0


@dataclass
class SessionSnapshot:
    """セッション全体（メタ + メッセージ）"""
    meta: SessionMeta
    messages: list[dict]


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _ensure_dirs():
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def _session_path(session_id: str, archived: bool = False) -> Path:
    base = ARCHIVE_DIR if archived else SESSION_DIR
    return base / f"{session_id}.json"


def _estimate_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        jp = sum(1 for c in content if ord(c) > 127)
        en = len(content) - jp
        total += int(jp * 1.5 + en * 0.3)
    return total


def _auto_title(messages: list[dict]) -> str:
    """最初のユーザーメッセージから短いタイトルを生成"""
    for msg in messages:
        if msg.get("role") == "user":
            content = str(msg.get("content", ""))
            title = content.replace("\n", " ").strip()
            return title[:50] + ("..." if len(title) > 50 else "")
    return "無題セッション"


def _auto_tags(messages: list[dict]) -> list[str]:
    """メッセージ内容からタグを自動付与"""
    text = " ".join(
        str(msg.get("content", ""))[:100]
        for msg in messages
        if msg.get("role") == "user"
    ).lower()

    tag_rules = {
        "coding":  ["コード", "実装", "python", "rust", "javascript", "デバッグ"],
        "space":   ["衛星", "宇宙", "orbit", "spacecraft", "jerg"],
        "search":  ["検索", "調べ", "教えて", "とは"],
        "analysis":["分析", "比較", "トレードオフ", "設計"],
    }

    tags = []
    for tag, keywords in tag_rules.items():
        if any(kw in text for kw in keywords):
            tags.append(tag)
    return tags


# ---------------------------------------------------------------------------
# インデックス管理
# ---------------------------------------------------------------------------

def _load_index() -> dict[str, dict]:
    """インデックスファイルを読み込む"""
    _ensure_dirs()
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_index(index: dict[str, dict]):
    _ensure_dirs()
    INDEX_FILE.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _update_index(meta: SessionMeta):
    index = _load_index()
    index[meta.session_id] = {
        "session_id":    meta.session_id,
        "created_at":    meta.created_at,
        "updated_at":    meta.updated_at,
        "message_count": meta.message_count,
        "title":         meta.title,
        "tags":          meta.tags,
        "archived":      meta.archived,
        "model":         meta.model,
        "total_tokens":  meta.total_tokens,
    }
    _save_index(index)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def save_session(
    session_id: str,
    messages: list[dict],
    model: str = "",
    tags: Optional[list[str]] = None,
) -> SessionMeta:
    """
    セッションを保存し、メタデータを返す。

    Args:
        session_id: セッションID
        messages: メッセージリスト
        model: 使用したモデル名
        tags: タグリスト（None なら自動付与）

    Returns:
        SessionMeta
    """
    _ensure_dirs()
    now = time.time()

    # 既存セッションの読み込み（更新日時を保持）
    existing_meta = get_session_meta(session_id)
    created_at = existing_meta.created_at if existing_meta else now

    meta = SessionMeta(
        session_id=session_id,
        created_at=created_at,
        updated_at=now,
        message_count=len(messages),
        title=_auto_title(messages),
        tags=tags if tags is not None else _auto_tags(messages),
        model=model,
        total_tokens=_estimate_tokens(messages),
    )

    data = {
        "meta": {
            "session_id":    meta.session_id,
            "created_at":    meta.created_at,
            "updated_at":    meta.updated_at,
            "message_count": meta.message_count,
            "title":         meta.title,
            "tags":          meta.tags,
            "model":         meta.model,
            "total_tokens":  meta.total_tokens,
        },
        "messages": messages,
    }

    path = _session_path(session_id)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _update_index(meta)

    # 自動アーカイブ
    _auto_archive_old_sessions()

    return meta


def load_session(session_id: str) -> Optional[SessionSnapshot]:
    """
    セッションを読み込む。アーカイブも検索する。

    Returns:
        SessionSnapshot or None
    """
    for archived in (False, True):
        path = _session_path(session_id, archived=archived)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                meta_dict = data.get("meta", {})
                meta = SessionMeta(**{k: v for k, v in meta_dict.items()
                                     if k in SessionMeta.__dataclass_fields__})
                return SessionSnapshot(meta=meta, messages=data.get("messages", []))
            except Exception as e:
                print(f"セッション読み込みエラー: {e}")
    return None


def delete_session(session_id: str) -> bool:
    """セッションを削除"""
    deleted = False
    for archived in (False, True):
        path = _session_path(session_id, archived=archived)
        if path.exists():
            path.unlink()
            deleted = True

    if deleted:
        index = _load_index()
        index.pop(session_id, None)
        _save_index(index)
    return deleted


def get_session_meta(session_id: str) -> Optional[SessionMeta]:
    """セッションのメタデータのみを取得（高速）"""
    index = _load_index()
    entry = index.get(session_id)
    if entry:
        return SessionMeta(**{k: v for k, v in entry.items()
                              if k in SessionMeta.__dataclass_fields__})
    return None


# ---------------------------------------------------------------------------
# 検索・一覧
# ---------------------------------------------------------------------------

def list_sessions(
    *,
    limit: int = 20,
    tags: Optional[list[str]] = None,
    query: Optional[str] = None,
    include_archived: bool = False,
) -> list[SessionMeta]:
    """
    セッション一覧を返す（最新順）。

    Args:
        limit: 最大件数
        tags: タグでフィルタ（OR条件）
        query: タイトルに含まれるキーワード
        include_archived: アーカイブ済みも含めるか

    Returns:
        SessionMeta のリスト
    """
    index = _load_index()
    sessions = []
    for entry in index.values():
        meta = SessionMeta(**{k: v for k, v in entry.items()
                              if k in SessionMeta.__dataclass_fields__})
        # フィルタ
        if not include_archived and meta.archived:
            continue
        if tags and not any(t in meta.tags for t in tags):
            continue
        if query and query.lower() not in meta.title.lower():
            continue
        sessions.append(meta)

    # 最新順にソート
    sessions.sort(key=lambda m: m.updated_at, reverse=True)
    return sessions[:limit]


def get_latest_session_id() -> Optional[str]:
    """最新のセッションIDを返す"""
    sessions = list_sessions(limit=1)
    return sessions[0].session_id if sessions else None


# ---------------------------------------------------------------------------
# コンテキスト引き継ぎ
# ---------------------------------------------------------------------------

def build_context_from_session(
    session_id: str,
    max_messages: int = 10,
    include_system: bool = True,
) -> list[dict]:
    """
    別セッションのメッセージを引き継ぎ用コンテキストとして整形。

    Returns:
        messages リスト
    """
    snapshot = load_session(session_id)
    if not snapshot:
        return []

    messages = snapshot.messages
    if include_system:
        system_msgs = [m for m in messages if m.get("role") == "system"]
        other_msgs = [m for m in messages if m.get("role") != "system"]
        kept = system_msgs[-1:] + other_msgs[-max_messages:]
    else:
        kept = [m for m in messages if m.get("role") != "system"][-max_messages:]

    return kept


def summarize_session_for_context(session_id: str, client=None, model: str = "") -> str:
    """
    セッション内容を要約文字列として返す（新セッション引き継ぎ用）。
    LLMクライアントが提供されれば LLM で要約、なければ最初のユーザーメッセージを返す。
    """
    snapshot = load_session(session_id)
    if not snapshot:
        return ""

    # LLMなし: タイトルと最初のユーザーメッセージ
    if client is None:
        return f"[前セッション: {snapshot.meta.title}]"

    # LLMで要約
    user_msgs = [
        m.get("content", "")[:200]
        for m in snapshot.messages
        if m.get("role") == "user"
    ]
    assistant_msgs = [
        m.get("content", "")[:200]
        for m in snapshot.messages
        if m.get("role") == "assistant"
    ]
    conversation = "\n".join(
        f"User: {u}\nAssistant: {a}"
        for u, a in zip(user_msgs[:3], assistant_msgs[:3])
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": f"以下の会話を100文字以内で要約してください:\n{conversation}"
            }],
            max_tokens=150,
        )
        return response.choices[0].message.content or snapshot.meta.title
    except Exception:
        return snapshot.meta.title


# ---------------------------------------------------------------------------
# アーカイブ
# ---------------------------------------------------------------------------

def _auto_archive_old_sessions():
    """古いセッションを自動アーカイブ"""
    import time as time_mod
    threshold = time_mod.time() - ARCHIVE_DAYS * 86400
    index = _load_index()

    for session_id, entry in list(index.items()):
        if entry.get("archived"):
            continue
        if entry.get("updated_at", 0) < threshold:
            _archive_session(session_id)

    # インデックスが上限を超えた場合、最古のものをアーカイブ
    non_archived = [e for e in index.values() if not e.get("archived")]
    if len(non_archived) > MAX_SESSIONS:
        oldest = sorted(non_archived, key=lambda e: e["updated_at"])
        for entry in oldest[:len(non_archived) - MAX_SESSIONS]:
            _archive_session(entry["session_id"])


def _archive_session(session_id: str):
    """セッションをアーカイブディレクトリに移動"""
    src = _session_path(session_id, archived=False)
    if not src.exists():
        return

    dst = _session_path(session_id, archived=True)
    src.rename(dst)

    index = _load_index()
    if session_id in index:
        index[session_id]["archived"] = True
        _save_index(index)


def archive_session(session_id: str) -> bool:
    """手動でセッションをアーカイブ"""
    _archive_session(session_id)
    return True


def restore_session(session_id: str) -> bool:
    """アーカイブからセッションを復元"""
    src = _session_path(session_id, archived=True)
    if not src.exists():
        return False

    dst = _session_path(session_id, archived=False)
    src.rename(dst)

    index = _load_index()
    if session_id in index:
        index[session_id]["archived"] = False
        _save_index(index)
    return True


# ---------------------------------------------------------------------------
# エクスポート
# ---------------------------------------------------------------------------

def export_to_markdown(session_id: str) -> Optional[str]:
    """セッションをMarkdown形式にエクスポート"""
    snapshot = load_session(session_id)
    if not snapshot:
        return None

    meta = snapshot.meta
    lines = [
        f"# {meta.title}",
        f"",
        f"- セッションID: `{meta.session_id}`",
        f"- モデル: {meta.model}",
        f"- メッセージ数: {meta.message_count}",
        f"- 推定トークン: {meta.total_tokens}",
        f"- タグ: {', '.join(meta.tags) if meta.tags else 'なし'}",
        f"",
        f"---",
        f"",
    ]

    for msg in snapshot.messages:
        role = msg.get("role", "unknown")
        content = msg.get("content") or ""

        if role == "system":
            lines.append(f"**[System Prompt]**")
            lines.append(f"```")
            lines.append(content[:500])
            lines.append(f"```")
            lines.append("")
        elif role == "user":
            lines.append(f"**User:**")
            lines.append(content)
            lines.append("")
        elif role == "assistant":
            lines.append(f"**Assistant:**")
            lines.append(content)
            lines.append("")
        elif role == "tool":
            lines.append(f"**[Tool Result]**")
            lines.append(f"```")
            lines.append(str(content)[:500])
            lines.append(f"```")
            lines.append("")

    return "\n".join(lines)


def save_export(session_id: str, output_dir: Optional[Path] = None) -> Optional[Path]:
    """セッションをMarkdownファイルとして保存"""
    md = export_to_markdown(session_id)
    if md is None:
        return None

    out_dir = output_dir or SESSION_DIR / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{session_id}.md"
    path.write_text(md, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# セッションID生成
# ---------------------------------------------------------------------------

def generate_session_id() -> str:
    """タイムスタンプベースのセッションIDを生成"""
    import datetime
    now = datetime.datetime.now()
    return now.strftime("session_%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# クイックテスト
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 基本的な save/load テスト
    sid = generate_session_id()
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "衛星の熱制御について教えて"},
        {"role": "assistant", "content": "衛星の熱制御は..."},
    ]

    meta = save_session(sid, messages, model="gemma2:27b")
    print(f"Saved: {meta.session_id}, title={meta.title!r}, tags={meta.tags}")

    snapshot = load_session(sid)
    if snapshot:
        print(f"Loaded: {len(snapshot.messages)} messages")

    sessions = list_sessions()
    print(f"Total sessions: {len(sessions)}")
    for s in sessions[:3]:
        print(f"  {s.session_id}: {s.title!r}")

    # Markdown エクスポートテスト
    md = export_to_markdown(sid)
    print(f"\nMarkdown preview:\n{md[:200] if md else 'None'}")

    # クリーンアップ
    delete_session(sid)
    print("\nTest passed.")
