"""
config.py - 設定管理モジュール

vLLM OpenAI互換API向けコーディングエージェントの設定を管理する。
環境変数またはデフォルト値から設定を読み込む。
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


@dataclass
class LLMConfig:
    """LLM接続設定"""

    # vLLM サーバーのエンドポイント（OpenAI互換）
    base_url: str = os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")

    # APIキー（vLLMはデフォルトで不要だが互換性のため）
    api_key: str = os.getenv("VLLM_API_KEY", "dummy-key")

    # 使用するモデル名（vLLMにロードしたモデル名）
    model: str = os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-Coder-32B-Instruct")

    # 最大生成トークン数
    max_tokens: int = int(os.getenv("MAX_TOKENS", "4096"))

    # 思考用の追加トークン（CoTプロンプト使用時）
    thinking_tokens: int = int(os.getenv("THINKING_TOKENS", "2048"))

    # 生成温度（コーディングタスクは低めが安定）
    temperature: float = float(os.getenv("TEMPERATURE", "0.1"))

    # top_p サンプリング
    top_p: float = float(os.getenv("TOP_P", "0.9"))

    # ツール使用をネイティブサポートするか（False の場合JSONパースにフォールバック）
    use_native_tool_call: bool = os.getenv("USE_NATIVE_TOOL_CALL", "true").lower() == "true"

    # リクエストタイムアウト（秒）
    timeout: int = int(os.getenv("REQUEST_TIMEOUT", "120"))


@dataclass
class AgentConfig:
    """エージェント動作設定"""

    # エージェントループの最大イテレーション数（無限ループ防止）
    max_iterations: int = int(os.getenv("MAX_ITERATIONS", "50"))

    # サブエージェントの最大並列数
    max_parallel_agents: int = int(os.getenv("MAX_PARALLEL_AGENTS", "5"))

    # コンテキスト圧縮を開始するトークン閾値
    context_compress_threshold: int = int(os.getenv("CONTEXT_COMPRESS_THRESHOLD", "30000"))

    # ツール実行のタイムアウト（秒）
    tool_timeout: int = int(os.getenv("TOOL_TIMEOUT", "120"))

    # Bashコマンドの最大タイムアウト（秒）
    bash_max_timeout: int = int(os.getenv("BASH_MAX_TIMEOUT", "600"))

    # ツール出力の最大文字数（切り詰め閾値）
    tool_output_max_chars: int = int(os.getenv("TOOL_OUTPUT_MAX_CHARS", "30000"))

    # 作業ディレクトリ
    work_dir: str = os.getenv("WORK_DIR", os.getcwd())

    # Extended Thinking相当の強制CoT（Trueで<thinking>タグを強制）
    force_chain_of_thought: bool = os.getenv("FORCE_COT", "true").lower() == "true"

    # 計画モードを有効にするか
    enable_plan_mode: bool = os.getenv("ENABLE_PLAN_MODE", "true").lower() == "true"

    # セッション履歴の保存先ディレクトリ
    session_dir: str = os.getenv("SESSION_DIR", "./sessions")

    # デバッグログを出力するか
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"


@dataclass
class Config:
    """全体設定"""
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)


# グローバル設定インスタンス（シングルトン）
_config: Optional[Config] = None


def get_config() -> Config:
    """設定を取得する（シングルトン）"""
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:
    """設定をリセットする（テスト用）"""
    global _config
    _config = None


# システムプロンプト定義
SYSTEM_PROMPT_BASE = """あなたは優秀なコーディングエージェントです。
ユーザーの要求を正確に理解し、適切なツールを使って問題を解決します。

## 基本原則
- 正確性を最優先する。推測で動作しない
- ファイルを読む前に変更しない
- 破壊的操作（削除、上書き）の前に確認を取る
- エラーが発生したら原因を調査してから再試行する
- 並列実行できる作業は積極的に並列化する

## 作業スタイル
- 簡潔に要点を答える。無駄な説明は省く
- コード例は実際に動くものを提供する
- 日本語で会話する（コードコメントも日本語可）
"""

SYSTEM_PROMPT_COT = """
## 思考プロセス（重要）
回答する前に、必ず以下の形式で思考を展開してください：

<thinking>
1. 問題の理解: ユーザーが何を求めているか
2. 現状の把握: 関連するファイル・コードの状態
3. 解決アプローチ: 複数の選択肢を検討し最善を選ぶ
4. 実行計画: ステップバイステップで何をするか
5. リスク評価: 破壊的な操作がないか確認
</thinking>

thinking タグの外に実際の回答・実行内容を書いてください。
"""

SYSTEM_PROMPT_TOOLS = """
## 利用可能なツール
- **Read**: ファイルを読む（編集前に必ず使う）
- **Write**: ファイルを新規作成または完全上書き
- **Edit**: ファイルの一部を編集（差分のみ送信）
- **Glob**: ファイルパターンで検索
- **Grep**: ファイル内容を正規表現で検索
- **Bash**: シェルコマンドを実行
- **WebSearch**: Webを検索
- **WebFetch**: URLのコンテンツを取得・解析

## ツール使用のルール
- ファイルを編集する前に必ず Read で内容を確認
- Write は新規作成のみ。既存ファイルは Edit を優先
- Bash でファイル検索より Glob/Grep を優先
- 並列実行可能なツール呼び出しは同時に行う
"""

PLAN_MODE_PROMPT = """
## 計画モード
あなたは今、計画モードで動作しています。
実際にツールを実行せず、以下の形式で実行計画のみを提示してください：

<plan>
## 目標
[達成すべき目標]

## 手順
1. [ステップ1の説明]
2. [ステップ2の説明]
...

## リスク・注意点
- [破壊的操作がある場合はここに記載]

## 確認事項
- [不明点があればここで質問]
</plan>

計画を提示したら、ユーザーの承認を待ってください。
"""

SUB_AGENT_SYSTEM_PROMPT = """あなたはサブエージェントです。
メインエージェントから委託された特定のタスクのみを実行します。
タスクが完了したら、結果を簡潔に報告してください。
エラーが発生した場合は詳細なエラー情報を含めて報告してください。
"""

# 宇宙分野RAGシステム設定
SPACE_RAG_SYSTEM_PROMPT = """あなたは宇宙・航空宇宙工学の専門家AIアシスタントです。
JAXA JERG文書、NASA技術報告書、ESA文書などの技術文書と
宇宙工学の知識に基づいて正確に回答します。

## 専門用語について
- 略語は初出時に展開してください（例: LEO → Low Earth Orbit（低軌道））
- 単位は必ず明記してください
- 不確かな情報には必ずその旨を明示してください

## 参照文書の使い方
提示された[参照文書]の内容を根拠として回答してください。
文書に記載がない場合は、知識から回答し「文書に記載なし」と明記してください。
"""
