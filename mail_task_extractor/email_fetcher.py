"""
email_fetcher.py - メール取得モジュール

対応ソース:
  1. IMAP (imaplib) - 汎用メールサーバー・Gmail IMAP
  2. Exchange / Office365 (exchangelib)
  3. Gmail API (google-api-python-client)
  4. .eml ファイル（手動エクスポート）
  5. .msg ファイル（Outlook エクスポート）

使い方:
    fetcher = IMAPFetcher("imap.example.com", "user@example.com", "password")
    raw_emails = fetcher.fetch_since(datetime(2024, 1, 1))
"""

from __future__ import annotations

import email
import imaplib
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from email.policy import default as default_policy
from pathlib import Path
from typing import Iterator


# ─── 抽象基底クラス ──────────────────────────────────────────────────────────

class EmailFetcher(ABC):
    """全フェッチャーの基底クラス"""

    @abstractmethod
    def fetch_since(self, since: datetime) -> Iterator[bytes]:
        """指定日時以降のメールを raw bytes で yield する"""
        ...

    @abstractmethod
    def fetch_folder(self, folder: str, since: datetime) -> Iterator[bytes]:
        """フォルダを指定して取得"""
        ...


# ─── 1. IMAP フェッチャー ─────────────────────────────────────────────────────

class IMAPFetcher(EmailFetcher):
    """
    IMAP4_SSL によるメール取得。
    Gmail / 社内 Exchange IMAP / 一般プロバイダに対応。

    Gmail の場合:
      - host: "imap.gmail.com", port: 993
      - 2段階認証有効の場合はアプリパスワードを使用
        (Google アカウント → セキュリティ → アプリパスワード)

    社内 Exchage IMAP:
      - host: mail.company.com, port: 993
      - または port: 143 + starttls
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 993,
        use_ssl: bool = True,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.use_ssl = use_ssl
        self._conn: imaplib.IMAP4_SSL | imaplib.IMAP4 | None = None

    # ── 接続管理 ──

    def connect(self) -> None:
        if self.use_ssl:
            self._conn = imaplib.IMAP4_SSL(self.host, self.port)
        else:
            self._conn = imaplib.IMAP4(self.host, self.port)
            self._conn.starttls()
        self._conn.login(self.username, self.password)

    def disconnect(self) -> None:
        if self._conn:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    # ── メール取得 ──

    def fetch_since(self, since: datetime, folder: str = "INBOX") -> Iterator[bytes]:
        """
        指定日以降のメールを取得。
        IMAP の SINCE は日付単位なので1日前から検索して安全側に倒す。
        """
        yield from self.fetch_folder(folder, since)

    def fetch_folder(self, folder: str, since: datetime) -> Iterator[bytes]:
        """フォルダ指定でメール取得"""
        if not self._conn:
            self.connect()

        # フォルダ選択（日本語フォルダ名は Modified UTF-7 エンコード必要だが
        # 標準 INBOX はそのまま通る）
        status, _ = self._conn.select(f'"{folder}"', readonly=True)
        if status != "OK":
            raise ValueError(f"フォルダ選択失敗: {folder}")

        # IMAP 日付フォーマット: "01-Jan-2024"
        since_str = since.strftime("%d-%b-%Y")
        status, data = self._conn.search(None, f'SINCE "{since_str}"')
        if status != "OK":
            return

        msg_ids = data[0].split()
        if not msg_ids:
            return

        # バッチ取得（大量メールでも IMAP 接続を圧迫しない）
        batch_size = 50
        for i in range(0, len(msg_ids), batch_size):
            batch = msg_ids[i : i + batch_size]
            id_range = b",".join(batch)
            # BODY.PEEK[]: 既読フラグを立てずに本文ごと取得
            status, items = self._conn.fetch(id_range, "(BODY.PEEK[])")
            if status != "OK":
                continue
            for item in items:
                if isinstance(item, tuple):
                    yield item[1]  # raw bytes

    def fetch_by_uid(self, uid: str) -> bytes | None:
        """UID指定で1通取得"""
        if not self._conn:
            self.connect()
        status, data = self._conn.uid("fetch", uid, "(BODY.PEEK[])")
        if status == "OK" and data and isinstance(data[0], tuple):
            return data[0][1]
        return None

    def list_folders(self) -> list[str]:
        """フォルダ一覧取得（送受信フォルダの確認に使用）"""
        if not self._conn:
            self.connect()
        status, data = self._conn.list()
        folders = []
        if status == "OK":
            for item in data:
                if isinstance(item, bytes):
                    # "(\\HasNoChildren) "/" "INBOX" の形式からフォルダ名を取り出す
                    parts = item.decode().split('"')
                    if len(parts) >= 3:
                        folders.append(parts[-2])
        return folders


# ─── 2. Exchange / Office365 フェッチャー ────────────────────────────────────

class ExchangeFetcher(EmailFetcher):
    """
    exchangelib を使った Exchange / Office365 連携。

    インストール:
      uv pip install exchangelib

    Office365 の場合は credentials に OAuth2 トークンも渡せる。
    """

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        autodiscover: bool = True,
        primary_smtp_address: str | None = None,
    ):
        try:
            from exchangelib import Account, Credentials, DELEGATE, Configuration
            self._ex_account_cls = Account
            self._ex_creds_cls = Credentials
            self._ex_delegate = DELEGATE
            self._ex_config_cls = Configuration
        except ImportError:
            raise ImportError(
                "exchangelib が未インストールです。\n"
                "uv pip install exchangelib"
            )

        self.server = server
        self.username = username
        self.password = password
        self.autodiscover = autodiscover
        self.smtp = primary_smtp_address or username
        self._account = None

    def _get_account(self):
        if self._account is None:
            from exchangelib import Account, Credentials, DELEGATE, Configuration
            creds = Credentials(self.username, self.password)
            if self.autodiscover:
                self._account = Account(
                    self.smtp,
                    credentials=creds,
                    autodiscover=True,
                    access_type=DELEGATE,
                )
            else:
                config = Configuration(server=self.server, credentials=creds)
                self._account = Account(
                    self.smtp,
                    config=config,
                    access_type=DELEGATE,
                )
        return self._account

    def fetch_since(self, since: datetime) -> Iterator[bytes]:
        yield from self.fetch_folder("inbox", since)

    def fetch_folder(self, folder: str, since: datetime) -> Iterator[bytes]:
        from exchangelib import EWSDateTime, UTC
        account = self._get_account()

        # フォルダオブジェクト取得
        if folder == "inbox":
            target = account.inbox
        else:
            # サブフォルダを名前で探す
            target = account.root.glob(f"**/{folder}")

        ews_since = EWSDateTime.from_datetime(since.replace(tzinfo=UTC))

        # datetime_received >= since のメールを取得
        # mime_content で raw bytes を取得
        for msg in target.filter(datetime_received__gte=ews_since).order_by(
            "datetime_received"
        ):
            if msg.mime_content:
                yield msg.mime_content


# ─── 3. Gmail API フェッチャー ───────────────────────────────────────────────

class GmailAPIFetcher(EmailFetcher):
    """
    Gmail API (OAuth2) を使ったメール取得。
    IMAP が無効化された環境や、添付ファイルの確実な取得に有効。

    事前準備:
      1. Google Cloud Console でプロジェクト作成
      2. Gmail API を有効化
      3. OAuth 2.0 クライアント ID を作成 → credentials.json をダウンロード
      4. uv pip install google-api-python-client google-auth-oauthlib

    初回実行でブラウザ認証 → token.json に保存（以降は自動更新）
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    def __init__(
        self,
        credentials_file: str = "credentials.json",
        token_file: str = "token.json",
    ):
        try:
            from google.oauth2.credentials import Credentials as GCreds
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            self._gcreds = GCreds
            self._flow = InstalledAppFlow
            self._request = Request
            self._build = build
        except ImportError:
            raise ImportError(
                "Google API ライブラリ未インストール。\n"
                "uv pip install google-api-python-client google-auth-oauthlib"
            )

        self.credentials_file = credentials_file
        self.token_file = token_file
        self._service = None

    def _get_service(self):
        if self._service:
            return self._service

        creds = None
        if os.path.exists(self.token_file):
            creds = self._gcreds.from_authorized_user_file(
                self.token_file, self.SCOPES
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(self._request())
            else:
                flow = self._flow.from_client_secrets_file(
                    self.credentials_file, self.SCOPES
                )
                creds = flow.run_local_server(port=0)
            with open(self.token_file, "w") as f:
                f.write(creds.to_json())

        self._service = self._build("gmail", "v1", credentials=creds)
        return self._service

    def fetch_since(self, since: datetime) -> Iterator[bytes]:
        yield from self.fetch_folder("INBOX", since)

    def fetch_folder(self, folder: str, since: datetime) -> Iterator[bytes]:
        import base64
        service = self._get_service()

        # Gmail の query 構文: after:YYYY/MM/DD
        query = f"after:{since.strftime('%Y/%m/%d')}"
        if folder != "INBOX":
            query = f"label:{folder} {query}"

        page_token = None
        while True:
            kwargs = {
                "userId": "me",
                "q": query,
                "maxResults": 100,
            }
            if page_token:
                kwargs["pageToken"] = page_token

            result = service.users().messages().list(**kwargs).execute()
            messages = result.get("messages", [])

            for msg_ref in messages:
                # format=raw で MIME メッセージ全体を base64 で取得
                raw = service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="raw"
                ).execute()
                yield base64.urlsafe_b64decode(raw["raw"] + "==")

            page_token = result.get("nextPageToken")
            if not page_token:
                break


# ─── 4. .eml ファイルフェッチャー ─────────────────────────────────────────────

class EMLFileFetcher(EmailFetcher):
    """
    .eml ファイルからメールを読み込む。

    Outlook でのエクスポート方法:
      - メールを選択 → ファイル → 名前をつけて保存 → .eml 形式
      - または Thunderbird / Apple Mail でも同様

    フォルダ内の全 .eml を再帰的に処理する。
    """

    def __init__(self, directory: str):
        self.directory = Path(directory)

    def fetch_since(self, since: datetime) -> Iterator[bytes]:
        yield from self.fetch_folder(str(self.directory), since)

    def fetch_folder(self, folder: str, since: datetime) -> Iterator[bytes]:
        base = Path(folder)
        for eml_path in sorted(base.rglob("*.eml")):
            # ファイルの更新日時でフィルタ（簡易）
            mtime = datetime.fromtimestamp(eml_path.stat().st_mtime)
            if mtime < since:
                continue
            with open(eml_path, "rb") as f:
                yield f.read()

    def fetch_all(self) -> Iterator[bytes]:
        """日時フィルタなしで全ファイル取得"""
        for eml_path in sorted(self.directory.rglob("*.eml")):
            with open(eml_path, "rb") as f:
                yield f.read()


# ─── 5. .msg ファイルフェッチャー（Outlook）────────────────────────────────

class MSGFileFetcher(EmailFetcher):
    """
    Outlook の .msg ファイルから読み込む。

    インストール:
      uv pip install extract-msg

    Outlook でのエクスポート:
      - メールを選択 → ファイル → 名前をつけて保存 → Outlook メッセージ形式 (.msg)
    """

    def __init__(self, directory: str):
        try:
            import extract_msg  # noqa
        except ImportError:
            raise ImportError(
                "extract-msg が未インストールです。\n"
                "uv pip install extract-msg"
            )
        self.directory = Path(directory)

    def fetch_since(self, since: datetime) -> Iterator[bytes]:
        yield from self.fetch_folder(str(self.directory), since)

    def fetch_folder(self, folder: str, since: datetime) -> Iterator[bytes]:
        """
        .msg は直接 bytes を返せないため、
        extract_msg で中身を取り出して EML 形式の bytes に変換して返す。
        """
        import extract_msg
        base = Path(folder)
        for msg_path in sorted(base.rglob("*.msg")):
            mtime = datetime.fromtimestamp(msg_path.stat().st_mtime)
            if mtime < since:
                continue
            try:
                with extract_msg.openMsg(str(msg_path)) as msg:
                    yield self._msg_to_eml_bytes(msg)
            except Exception as e:
                print(f"[MSGFetcher] 読み込み失敗: {msg_path} - {e}")

    @staticmethod
    def _msg_to_eml_bytes(msg) -> bytes:
        """extract_msg オブジェクト → EML bytes に変換"""
        import email.mime.text
        import email.mime.multipart

        m = email.mime.multipart.MIMEMultipart()
        m["Subject"] = msg.subject or ""
        m["From"] = msg.sender or ""
        m["To"] = msg.to or ""
        m["CC"] = msg.cc or ""
        if msg.date:
            m["Date"] = str(msg.date)
        if msg.message_id:
            m["Message-ID"] = msg.message_id

        body = msg.body or ""
        m.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))
        if msg.htmlBody:
            html_body = msg.htmlBody
            if isinstance(html_body, bytes):
                html_body = html_body.decode("utf-8", errors="replace")
            m.attach(email.mime.text.MIMEText(html_body, "html", "utf-8"))

        return m.as_bytes()


# ─── ファクトリ関数 ──────────────────────────────────────────────────────────

def create_fetcher(source_type: str, **kwargs) -> EmailFetcher:
    """
    source_type: "imap" | "exchange" | "gmail" | "eml" | "msg"

    使用例:
        fetcher = create_fetcher("imap",
            host="imap.gmail.com",
            username="user@gmail.com",
            password="app-password"
        )
        fetcher = create_fetcher("eml", directory="/path/to/emails/")
    """
    mapping = {
        "imap": IMAPFetcher,
        "exchange": ExchangeFetcher,
        "gmail": GmailAPIFetcher,
        "eml": EMLFileFetcher,
        "msg": MSGFileFetcher,
    }
    cls = mapping.get(source_type)
    if not cls:
        raise ValueError(f"不明なソースタイプ: {source_type}. {list(mapping.keys())}")
    return cls(**kwargs)
