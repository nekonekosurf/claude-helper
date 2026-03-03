"""
email_parser.py - メール解析・正規化モジュール

機能:
  - ヘッダー情報の抽出 (From/To/CC/Date/Subject/Message-ID)
  - HTML→プレーンテキスト変換
  - 日本語エンコーディング自動検出 (iso-2022-jp, shift_jis, euc-jp, utf-8)
  - 添付ファイル抽出・分類
  - スレッドグループ化 (Message-ID / In-Reply-To / References)
  - 日本語ビジネスメール特有の正規化
"""

from __future__ import annotations

import email
import hashlib
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from email import header as email_header
from email.policy import default as default_policy
from pathlib import Path
from typing import Optional

from .models import AttachmentType, EmailAttachment, EmailThread, ParsedEmail


# ─── 日本語エンコーディング処理 ──────────────────────────────────────────────

# よく使われる日本語メールエンコーディング（優先順位順）
JP_ENCODINGS = [
    "utf-8",
    "iso-2022-jp",   # JIS: 社内メールで多い
    "shift_jis",     # Outlook デフォルト
    "euc-jp",
    "cp932",         # Windows-31J (Shift_JIS の Microsoft 拡張)
    "latin-1",       # フォールバック
]

def decode_bytes_jp(data: bytes, declared_charset: str | None = None) -> str:
    """
    バイト列を日本語を考慮してデコード。
    宣言されている charset を優先し、失敗したら chardet → JP_ENCODINGS でフォールバック。
    """
    if not data:
        return ""

    # 宣言 charset を最優先
    if declared_charset:
        normalized = declared_charset.lower().replace("-", "_")
        # cp932 / sjis 系の正規化
        if normalized in ("shift_jis", "sjis", "x_sjis"):
            normalized = "cp932"
        try:
            return data.decode(normalized, errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass

    # chardet による自動検出
    try:
        import chardet
        detected = chardet.detect(data)
        if detected["encoding"] and detected["confidence"] > 0.6:
            enc = detected["encoding"]
            if enc.lower() in ("shift_jis", "x-sjis"):
                enc = "cp932"
            try:
                return data.decode(enc, errors="replace")
            except (LookupError, UnicodeDecodeError):
                pass
    except ImportError:
        pass

    # フォールバック: 順番に試す
    for enc in JP_ENCODINGS:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    return data.decode("utf-8", errors="replace")


def decode_header_value(raw: str | bytes | None) -> str:
    """
    RFC 2047 エンコードされたヘッダー値をデコード。
    Subject: =?iso-2022-jp?B?...?= のような形式を処理。
    """
    if not raw:
        return ""

    decoded_parts = email_header.decode_header(str(raw))
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(decode_bytes_jp(part, charset))
        else:
            result.append(str(part))
    return "".join(result)


# ─── HTML → プレーンテキスト ──────────────────────────────────────────────────

def html_to_text(html: str) -> str:
    """
    HTML をプレーンテキストに変換。
    beautifulsoup4 が使える場合はそれを使い、なければ正規表現で簡易処理。
    """
    if not html:
        return ""

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # script / style タグを除去
        for tag in soup(["script", "style", "head"]):
            tag.decompose()

        # <br> / <p> / <div> を改行に変換
        for tag in soup.find_all(["br", "p", "div", "tr"]):
            tag.insert_after("\n")

        text = soup.get_text(separator=" ")
    except ImportError:
        # フォールバック: 正規表現
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"&amp;", "&", text)

    # 余分な空白行を圧縮
    lines = [line.strip() for line in text.splitlines()]
    non_empty = []
    prev_empty = False
    for line in lines:
        is_empty = not line
        if is_empty and prev_empty:
            continue
        non_empty.append(line)
        prev_empty = is_empty

    return "\n".join(non_empty).strip()


# ─── 日本語ビジネスメール正規化 ──────────────────────────────────────────────

# 引用符パターン（> 形式 / 「------- Original Message -------」形式）
QUOTE_PATTERNS = [
    re.compile(r"^>.*$", re.MULTILINE),
    re.compile(r"^On .+ wrote:$", re.MULTILINE),
    re.compile(r"^-{3,}.*Original Message.*-{3,}$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^_{3,}$", re.MULTILINE),
    # 日本語引用
    re.compile(r"^.*転送メッセージ.*$", re.MULTILINE),
    re.compile(r"^\d{4}/\d{2}/\d{2}.+のメッセージ:$", re.MULTILINE),
    re.compile(r"^----.*メッセージ.*----$", re.MULTILINE),
    # 定型署名
    re.compile(r"^-{2,}\s*$", re.MULTILINE),
]

def strip_quoted_text(text: str) -> tuple[str, str]:
    """
    引用部分を除去し、(本文, 引用部分) のタプルを返す。
    引用部分はステータス更新の検出に使うので保持する。
    """
    # 最初の引用開始位置を探す
    first_quote_pos = len(text)
    for pattern in QUOTE_PATTERNS:
        m = pattern.search(text)
        if m and m.start() < first_quote_pos:
            first_quote_pos = m.start()

    body = text[:first_quote_pos].strip()
    quoted = text[first_quote_pos:].strip()
    return body, quoted


def normalize_jp_business_text(text: str) -> str:
    """
    日本語ビジネスメール特有の表現を正規化。
    敬語の意味は変えず、構造解析しやすい形にする。
    """
    # 全角英数字→半角
    text = unicodedata.normalize("NFKC", text)

    # よくある定型フレーズの正規化（意味は保持）
    patterns = [
        # 挨拶（本文解析には不要）
        (r"お世話になっております[。、]?\s*", ""),
        (r"いつもお世話になっております[。、]?\s*", ""),
        (r"ご確認のほど、?よろしく(お願いいたします|お願い申し上げます)[。]?\s*", "【確認依頼】"),
        (r"ご対応(のほど)?、?よろしく(お願いいたします|お願い申し上げます)[。]?\s*", "【対応依頼】"),
        # 締め言葉（LLM に渡す前に除去）
        (r"以上、?よろしく(お願いいたします|お願い申し上げます)[。]?\s*$", ""),
        (r"何卒よろしくお願い(いたします|申し上げます)[。]?\s*$", ""),
    ]

    for pat, repl in patterns:
        text = re.sub(pat, repl, text, flags=re.MULTILINE)

    # 連続する空白・改行を圧縮
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ─── メールパーサー ───────────────────────────────────────────────────────────

class EmailParser:
    """raw bytes の MIME メッセージを ParsedEmail に変換する"""

    def __init__(self, source: str = "unknown"):
        self.source = source

    def parse(self, raw: bytes) -> ParsedEmail | None:
        """
        raw bytes → ParsedEmail。
        パース失敗した場合は None を返す。
        """
        try:
            msg = email.message_from_bytes(raw, policy=default_policy)
            return self._extract(msg)
        except Exception as e:
            print(f"[EmailParser] パース失敗: {e}")
            return None

    def _extract(self, msg: email.message.Message) -> ParsedEmail:
        # ── ヘッダー ──
        subject = decode_header_value(msg.get("Subject", ""))
        from_raw = decode_header_value(msg.get("From", ""))
        to_raw = decode_header_value(msg.get("To", ""))
        cc_raw = decode_header_value(msg.get("CC", ""))
        date_str = msg.get("Date", "")
        message_id = msg.get("Message-ID", "").strip("<>")
        in_reply_to = msg.get("In-Reply-To", "").strip("<>") or None
        references_raw = msg.get("References", "")

        # Message-ID がない場合は Subject + Date からハッシュで生成
        if not message_id:
            message_id = hashlib.md5(
                f"{subject}{from_raw}{date_str}".encode()
            ).hexdigest()

        # From アドレスのパース
        sender_name, sender_address = self._parse_address(from_raw)

        # To / CC のリスト化
        recipients = self._parse_address_list(to_raw)
        cc = self._parse_address_list(cc_raw)

        # References ヘッダー: スペース区切りの Message-ID リスト
        references = [
            r.strip("<>")
            for r in references_raw.split()
            if r.strip("<>")
        ]

        # 日付パース
        date = self._parse_date(date_str)

        # ── ボディと添付ファイル ──
        body_text, body_html, attachments = self._extract_parts(msg)

        # 引用部分を分離（本文のみを正規化）
        body_clean, _ = strip_quoted_text(body_text)
        body_normalized = normalize_jp_business_text(body_clean)

        return ParsedEmail(
            message_id=message_id,
            subject=subject,
            sender=from_raw,
            sender_name=sender_name,
            sender_address=sender_address,
            recipients=recipients,
            cc=cc,
            date=date,
            body_text=body_normalized,
            body_html=body_html,
            in_reply_to=in_reply_to,
            references=references,
            attachments=attachments,
            raw_source=self.source,
        )

    def _parse_address(self, raw: str) -> tuple[str, str]:
        """
        "田中 太郎 <tanaka@example.com>" → ("田中 太郎", "tanaka@example.com")
        "tanaka@example.com" → ("", "tanaka@example.com")
        """
        m = re.match(r"^(.*?)\s*<([^>]+)>$", raw.strip())
        if m:
            name = m.group(1).strip().strip('"')
            addr = m.group(2).strip()
            return name, addr
        # アドレスのみ
        addr = raw.strip()
        return "", addr

    def _parse_address_list(self, raw: str) -> list[str]:
        """カンマ区切りのアドレスリストをパース"""
        if not raw:
            return []
        # 簡易パース: "," で split（名前中のカンマは考慮しないが実用上十分）
        parts = re.split(r",(?=[^<>]*(?:<|$))", raw)
        result = []
        for part in parts:
            part = part.strip()
            if part:
                _, addr = self._parse_address(part)
                if addr and "@" in addr:
                    result.append(addr)
        return result

    def _parse_date(self, date_str: str) -> datetime:
        """メールの Date ヘッダーを datetime に変換"""
        from email.utils import parsedate_to_datetime
        try:
            return parsedate_to_datetime(date_str)
        except Exception:
            return datetime.now(tz=timezone.utc)

    def _extract_parts(
        self, msg: email.message.Message
    ) -> tuple[str, str, list[EmailAttachment]]:
        """
        マルチパートメッセージを再帰的に処理し、
        (plain_text, html_text, attachments) を返す。
        """
        text_parts: list[str] = []
        html_parts: list[str] = []
        attachments: list[EmailAttachment] = []

        self._walk_parts(msg, text_parts, html_parts, attachments)

        body_text = "\n\n".join(text_parts)
        body_html = "\n".join(html_parts)

        # プレーンテキストがなければ HTML から変換
        if not body_text and body_html:
            body_text = html_to_text(body_html)

        return body_text, body_html, attachments

    def _walk_parts(
        self,
        part: email.message.Message,
        text_parts: list[str],
        html_parts: list[str],
        attachments: list[EmailAttachment],
    ) -> None:
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", "")).lower()

        if part.is_multipart():
            for subpart in part.get_payload(decode=False):
                if hasattr(subpart, "get_content_type"):
                    self._walk_parts(subpart, text_parts, html_parts, attachments)
            return

        # 添付ファイル判定
        is_attachment = "attachment" in disposition or "inline" in disposition
        filename = part.get_filename()
        if filename:
            filename = decode_header_value(filename)
            is_attachment = True

        if is_attachment and filename:
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                EmailAttachment(
                    filename=filename,
                    content_type=content_type,
                    size_bytes=len(payload),
                    attachment_type=self._classify_attachment(filename),
                )
            )
            return

        # テキスト本文
        payload = part.get_payload(decode=True)
        if payload is None:
            return

        charset = part.get_content_charset()
        text = decode_bytes_jp(payload, charset)

        if content_type == "text/plain":
            text_parts.append(text)
        elif content_type == "text/html":
            html_parts.append(text)

    @staticmethod
    def _classify_attachment(filename: str) -> AttachmentType:
        suffix = Path(filename).suffix.lower()
        mapping = {
            ".pdf": AttachmentType.PDF,
            ".xlsx": AttachmentType.EXCEL,
            ".xls": AttachmentType.EXCEL,
            ".csv": AttachmentType.EXCEL,
            ".docx": AttachmentType.WORD,
            ".doc": AttachmentType.WORD,
            ".png": AttachmentType.IMAGE,
            ".jpg": AttachmentType.IMAGE,
            ".jpeg": AttachmentType.IMAGE,
            ".gif": AttachmentType.IMAGE,
        }
        return mapping.get(suffix, AttachmentType.OTHER)


# ─── スレッドグループ化 ──────────────────────────────────────────────────────

class ThreadGrouper:
    """
    JWZ アルゴリズム簡易実装。
    Message-ID / In-Reply-To / References を使ってスレッドを構築する。

    参考: https://www.jwz.org/doc/threading.html
    """

    STALE_DAYS = 7  # 最終返信から何日で「放置」とみなすか

    def group(self, emails: list[ParsedEmail]) -> list[EmailThread]:
        """メールリストをスレッドにグループ化"""
        # Step 1: Message-ID → メール のマップ
        id_to_email: dict[str, ParsedEmail] = {}
        for em in emails:
            id_to_email[em.message_id] = em

        # Step 2: 親子関係を構築
        # parent[child_id] = parent_id
        parent: dict[str, str | None] = {}
        for em in emails:
            em_parent = self._find_parent(em, id_to_email)
            parent[em.message_id] = em_parent

        # Step 3: 根(root)メールを収集
        # subject の正規化で同一スレッドを統合
        root_subjects: dict[str, str] = {}  # root_id → normalized_subject

        def find_root(mid: str) -> str:
            visited = set()
            while parent.get(mid) and parent[mid] not in visited:
                visited.add(mid)
                mid = parent[mid]
            return mid

        # Step 4: root ごとにスレッドを構築
        thread_map: dict[str, list[str]] = defaultdict(list)  # root_id → [msg_ids]
        for em in emails:
            root_id = find_root(em.message_id)
            thread_map[root_id].append(em.message_id)

        # Step 5: スレッドオブジェクト生成
        threads: list[EmailThread] = []
        for root_id, msg_ids in thread_map.items():
            thread_emails = [
                id_to_email[mid] for mid in msg_ids if mid in id_to_email
            ]
            if not thread_emails:
                continue

            thread_emails.sort(key=lambda e: e.date)
            root_email = id_to_email.get(root_id, thread_emails[0])

            # スレッド ID: root の Message-ID をそのまま使う
            thread_id = root_id

            # 参加者収集
            participants: set[str] = set()
            for em in thread_emails:
                if em.sender_address:
                    participants.add(em.sender_address)
                participants.update(em.recipients)
                participants.update(em.cc)

            # スレッド ID を各メールに付与
            for em in thread_emails:
                em.thread_id = thread_id

            last_activity = thread_emails[-1].date
            is_stale = (
                datetime.now(tz=last_activity.tzinfo) - last_activity
            ).days > self.STALE_DAYS

            # Subject の正規化: "Re: ", "FW: ", "Fwd: " を除去
            subject = re.sub(
                r"^(Re:|FW:|Fwd:|転送:|RE:|FWD:)\s*",
                "",
                root_email.subject,
                flags=re.IGNORECASE,
            ).strip()

            threads.append(
                EmailThread(
                    thread_id=thread_id,
                    subject=subject,
                    emails=thread_emails,
                    participants=sorted(participants),
                    started_at=thread_emails[0].date,
                    last_activity=last_activity,
                    is_stale=is_stale,
                )
            )

        # 最終活動日の新しい順でソート
        threads.sort(key=lambda t: t.last_activity or datetime.min, reverse=True)
        return threads

    def _find_parent(
        self, em: ParsedEmail, id_to_email: dict[str, ParsedEmail]
    ) -> str | None:
        """
        In-Reply-To → References の末尾 の順で親を探す。
        存在するメールの Message-ID のみを親として採用。
        """
        # In-Reply-To が最も信頼性が高い
        if em.in_reply_to and em.in_reply_to in id_to_email:
            return em.in_reply_to

        # References の末尾（最も近い祖先）
        for ref in reversed(em.references):
            if ref in id_to_email:
                return ref

        return None
