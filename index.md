---
layout: default
title: Home
---

# Claude Helper

Raspberry Pi 上で動作する **ローカルAIコーディングエージェント**。
Cerebras API（gpt-oss-120b）を使用し、JAXA JERG技術文書96件に対する質問応答・検索・分析を行う。

---

## アーキテクチャ

```
ユーザー
  │
  ▼
┌─────────────────────────────────┐
│  Agent Loop (ReAct)             │
│  ┌───────────┐  ┌────────────┐ │
│  │ Planner   │  │ Tools (7種)│ │
│  │ (複雑な   │  │ read/write │ │
│  │  質問用)  │  │ edit/bash  │ │
│  └─────┬─────┘  │ glob/grep  │ │
│        │        │ search_docs│ │
│        ▼        └─────┬──────┘ │
│  ┌───────────┐        │        │
│  │ LLM Client├────────┘        │
│  │ (Cerebras)│                  │
│  └───────────┘                  │
└─────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────┐
│  検索エンジン                    │
│                                  │
│  Guided Retrieval (2段階)        │
│  ┌──────────┐ ┌───────────────┐ │
│  │Domain Map│→│Hybrid Search  │ │
│  │(36domain)│ │ BM25          │ │
│  │Glossary  │ │ 同義語展開    │ │
│  │(123 term)│ │ LLM展開       │ │
│  └──────────┘ │ チャンク要約  │ │
│               │ ベクトル検索  │ │
│               └───────────────┘ │
└─────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────┐
│  JERG文書インデックス            │
│  96文書 / 11,462チャンク         │
│  11,462 embeddings (384次元)     │
│  498 チャンク要約                │
└─────────────────────────────────┘
```

---

## 主な機能

| 機能 | 説明 |
|---|---|
| **エージェントループ** | ReActパターンによるツール実行ループ |
| **7種のツール** | read_file, write_file, edit_file, bash, glob, grep, search_docs |
| **ハイブリッド検索** | BM25 + 同義語 + LLM展開 + 要約 + ベクトルの5手法統合 |
| **ガイド付き検索** | ドメイン検出 → 文書絞り込み → 精密検索 |
| **Plan-Verify-Execute** | 複雑な質問を計画→検証→実行で安全に処理 |
| **ドメイン知識ベース** | 36ドメイン、123語辞書、10決定木、4手順書 |
| **適応プロンプト** | /teach で自然言語による知識追加 |
| **セッション管理** | --continue / --resume で会話継続 |
| **コンテキスト圧縮** | 古い会話を自動要約してトークン節約 |

---

## ドキュメント

- [実装計画](docs/implementation-plan.md) - Phase 1〜4 の設計と実装方針
- [ローカルLM設計書](docs/local-claude-code.md) - vLLM/Cerebras 基盤の詳細設計
- [適応プロンプト設計](docs/adaptive-prompt-design.md) - /teach によるドメイン知識蓄積
- [Claude Code アーキテクチャ](docs/claude-code-architecture.md) - 参考：本家の内部構造解析
- [チャンク品質改善設計](docs/chunk-improvement-design.md) - 検索精度向上のための3手法比較

---

## 技術スタック

| 項目 | 技術 |
|---|---|
| 実行環境 | Raspberry Pi (Linux ARM64) |
| 言語 | Python 3.11 |
| パッケージ管理 | UV |
| LLM | gpt-oss-120b (Cerebras API) / vLLM切替可 |
| 日本語トークナイザ | fugashi (MeCab) |
| embedding | fastembed (paraphrase-multilingual-MiniLM-L12-v2) |
| API | OpenAI互換 (base_url変更のみでvLLM切替) |

---

## 現在のステータス

| 項目 | 状態 |
|---|---|
| Phase 1: 基本エージェント | 完了 |
| Phase 2: JERG文書検索 | 完了 |
| Phase 3: セッション管理 | 完了 |
| Phase 4: 拡張ツール | 完了 |
| ハイブリッド検索 (5手法) | 完了 |
| ガイド付き検索 | 完了 |
| Plan-Verify-Execute | 完了 |
| ドメイン知識ベース | 完了 |
| ベクトルembedding | 完了 (11,462件) |
| チャンク要約 | 498/11,462件 (4%) |
| チャンク品質改善 | 設計完了、実装待ち |
