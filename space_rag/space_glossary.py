"""
宇宙・航空宇宙専門用語辞書

略語展開、日英対訳、用語間の関連性を管理する。
ローカルLLMエージェントがクエリ理解と回答生成に活用する。
"""

from __future__ import annotations
from dataclasses import dataclass, field


# ============================================================
# 略語辞書: 宇宙分野の略語 → 正式名称（英語）+ 日本語訳
# ============================================================
ABBREVIATIONS: dict[str, dict] = {
    # --- 軌道・位置 ---
    "LEO":  {"full": "Low Earth Orbit",              "ja": "低軌道",               "category": "orbit"},
    "MEO":  {"full": "Medium Earth Orbit",            "ja": "中軌道",               "category": "orbit"},
    "GEO":  {"full": "Geostationary Orbit",           "ja": "静止軌道",             "category": "orbit"},
    "GTO":  {"full": "Geostationary Transfer Orbit",  "ja": "静止遷移軌道",         "category": "orbit"},
    "HEO":  {"full": "Highly Elliptical Orbit",       "ja": "高楕円軌道",           "category": "orbit"},
    "SSO":  {"full": "Sun-Synchronous Orbit",         "ja": "太陽同期軌道",         "category": "orbit"},
    "L1":   {"full": "Lagrange Point 1",              "ja": "ラグランジュ点L1",     "category": "orbit"},
    "L2":   {"full": "Lagrange Point 2",              "ja": "ラグランジュ点L2",     "category": "orbit"},
    "L4":   {"full": "Lagrange Point 4",              "ja": "ラグランジュ点L4",     "category": "orbit"},
    "L5":   {"full": "Lagrange Point 5",              "ja": "ラグランジュ点L5",     "category": "orbit"},

    # --- 姿勢・軌道制御 ---
    "ADCS": {"full": "Attitude Determination and Control System", "ja": "姿勢決定制御系", "category": "aocs"},
    "AOCS": {"full": "Attitude and Orbit Control System",         "ja": "姿勢軌道制御系", "category": "aocs"},
    "RCS":  {"full": "Reaction Control System",       "ja": "姿勢制御スラスタ系",   "category": "propulsion"},
    "CMG":  {"full": "Control Moment Gyroscope",      "ja": "コントロールモーメントジャイロ", "category": "aocs"},
    "RW":   {"full": "Reaction Wheel",                "ja": "リアクションホイール",  "category": "aocs"},
    "MTQ":  {"full": "Magnetorquer",                  "ja": "マグネットトルカ",      "category": "aocs"},

    # --- 推進 ---
    "Isp":  {"full": "Specific Impulse",              "ja": "比推力",               "category": "propulsion"},
    "GNC":  {"full": "Guidance Navigation and Control","ja": "誘導・航法・制御",    "category": "guidance"},
    "LH2":  {"full": "Liquid Hydrogen",               "ja": "液体水素",             "category": "propulsion"},
    "LOX":  {"full": "Liquid Oxygen",                 "ja": "液体酸素",             "category": "propulsion"},
    "MMH":  {"full": "Monomethyl Hydrazine",          "ja": "モノメチルヒドラジン", "category": "propulsion"},
    "NTO":  {"full": "Nitrogen Tetroxide",            "ja": "四酸化二窒素",         "category": "propulsion"},

    # --- 通信 ---
    "TTC":  {"full": "Telemetry Tracking and Command","ja": "テレメトリ・追跡・コマンド", "category": "comms"},
    "TM":   {"full": "Telemetry",                     "ja": "テレメトリ",           "category": "comms"},
    "TC":   {"full": "Telecommand",                   "ja": "テレコマンド",         "category": "comms"},
    "RF":   {"full": "Radio Frequency",               "ja": "無線周波数",           "category": "comms"},
    "EIRP": {"full": "Equivalent Isotropic Radiated Power", "ja": "等価等方輻射電力", "category": "comms"},
    "CCSDS":{"full": "Consultative Committee for Space Data Systems", "ja": "宇宙データシステム諮問委員会", "category": "comms"},
    "TLM":  {"full": "Telemetry",                     "ja": "テレメトリ",           "category": "comms"},

    # --- 電力 ---
    "EPS":  {"full": "Electrical Power Subsystem",    "ja": "電力系",               "category": "power"},
    "PCDU": {"full": "Power Conditioning and Distribution Unit", "ja": "電力調整配電ユニット", "category": "power"},
    "SAR":  {"full": "Solar Array",                   "ja": "太陽電池パドル",       "category": "power"},
    "DOD":  {"full": "Depth of Discharge",            "ja": "放電深度",             "category": "power"},
    "SOC":  {"full": "State of Charge",               "ja": "充電状態",             "category": "power"},

    # --- 熱制御 ---
    "TCS":  {"full": "Thermal Control System",        "ja": "熱制御系",             "category": "thermal"},
    "MLI":  {"full": "Multi-Layer Insulation",        "ja": "多層断熱材",           "category": "thermal"},
    "LHP":  {"full": "Loop Heat Pipe",                "ja": "ループヒートパイプ",   "category": "thermal"},
    "VCL":  {"full": "Variable Conductance Link",     "ja": "可変コンダクタンスリンク", "category": "thermal"},
    "OSR":  {"full": "Optical Solar Reflector",       "ja": "光学太陽反射鏡",       "category": "thermal"},

    # --- 構造 ---
    "CFRP": {"full": "Carbon Fiber Reinforced Plastic","ja": "炭素繊維強化プラスチック", "category": "structures"},
    "GFRP": {"full": "Glass Fiber Reinforced Plastic","ja": "ガラス繊維強化プラスチック", "category": "structures"},
    "FEM":  {"full": "Finite Element Model",          "ja": "有限要素モデル",       "category": "structures"},
    "FEA":  {"full": "Finite Element Analysis",       "ja": "有限要素解析",         "category": "structures"},
    "CLA":  {"full": "Coupled Loads Analysis",        "ja": "連成荷重解析",         "category": "structures"},

    # --- ミッション・開発フェーズ ---
    "PDR":  {"full": "Preliminary Design Review",     "ja": "基本設計審査",         "category": "development"},
    "CDR":  {"full": "Critical Design Review",        "ja": "詳細設計審査",         "category": "development"},
    "SRR":  {"full": "System Requirements Review",    "ja": "システム要求審査",     "category": "development"},
    "SDR":  {"full": "System Design Review",          "ja": "システム設計審査",     "category": "development"},
    "SIL":  {"full": "Software Integration Laboratory","ja": "ソフトウェア統合ラボ","category": "development"},
    "FRR":  {"full": "Flight Readiness Review",       "ja": "飛行準備審査",         "category": "development"},
    "PRR":  {"full": "Production Readiness Review",   "ja": "製造準備審査",         "category": "development"},
    "AIV":  {"full": "Assembly Integration and Verification","ja": "組立・結合・試験", "category": "development"},
    "AIT":  {"full": "Assembly Integration and Testing","ja": "組立・結合・試験",   "category": "development"},

    # --- 試験 ---
    "EMC":  {"full": "Electromagnetic Compatibility", "ja": "電磁適合性",           "category": "testing"},
    "EMI":  {"full": "Electromagnetic Interference",  "ja": "電磁干渉",             "category": "testing"},
    "ESD":  {"full": "Electrostatic Discharge",       "ja": "静電気放電",           "category": "testing"},
    "TVT":  {"full": "Thermal Vacuum Test",           "ja": "熱真空試験",           "category": "testing"},
    "SV":   {"full": "Sine Vibration",                "ja": "正弦波振動",           "category": "testing"},
    "RV":   {"full": "Random Vibration",              "ja": "ランダム振動",         "category": "testing"},
    "QT":   {"full": "Qualification Test",            "ja": "認定試験",             "category": "testing"},
    "AT":   {"full": "Acceptance Test",               "ja": "受入試験",             "category": "testing"},

    # --- 信頼性・安全 ---
    "FMEA": {"full": "Failure Mode and Effects Analysis","ja": "故障モード影響解析", "category": "reliability"},
    "FMECA":{"full": "Failure Mode Effects and Criticality Analysis","ja": "故障モード影響・重大度解析", "category": "reliability"},
    "FTA":  {"full": "Fault Tree Analysis",           "ja": "フォールトツリー解析", "category": "reliability"},
    "MTBF": {"full": "Mean Time Between Failures",    "ja": "平均故障間隔",         "category": "reliability"},
    "MTTF": {"full": "Mean Time To Failure",          "ja": "平均故障時間",         "category": "reliability"},
    "RPN":  {"full": "Risk Priority Number",          "ja": "リスク優先数",         "category": "reliability"},

    # --- 機関・規格 ---
    "JAXA": {"full": "Japan Aerospace Exploration Agency","ja": "宇宙航空研究開発機構", "category": "organization"},
    "NASA": {"full": "National Aeronautics and Space Administration","ja": "米国航空宇宙局", "category": "organization"},
    "ESA":  {"full": "European Space Agency",         "ja": "欧州宇宙機関",         "category": "organization"},
    "ECSS": {"full": "European Cooperation for Space Standardization","ja": "欧州宇宙標準化協力", "category": "standard"},
    "JERG": {"full": "JAXA Engineering Reference Guide","ja": "JAXA技術参照文書",  "category": "standard"},
    "TRL":  {"full": "Technology Readiness Level",    "ja": "技術成熟度",           "category": "development"},
    "MRL":  {"full": "Manufacturing Readiness Level", "ja": "製造成熟度",           "category": "development"},

    # --- ペイロード・観測 ---
    "GSD":  {"full": "Ground Sampling Distance",      "ja": "地上分解能",           "category": "payload"},
    "FOV":  {"full": "Field of View",                 "ja": "視野角",               "category": "payload"},
    "IFOV": {"full": "Instantaneous Field of View",   "ja": "瞬時視野角",           "category": "payload"},
    "SNR":  {"full": "Signal to Noise Ratio",         "ja": "信号対雑音比",         "category": "payload"},
    "MTF":  {"full": "Modulation Transfer Function",  "ja": "変調伝達関数",         "category": "payload"},

    # --- 打ち上げ・運用 ---
    "LV":   {"full": "Launch Vehicle",                "ja": "打上げロケット",       "category": "launch"},
    "SCM":  {"full": "Spacecraft",                    "ja": "宇宙機",               "category": "spacecraft"},
    "S/C":  {"full": "Spacecraft",                    "ja": "宇宙機",               "category": "spacecraft"},
    "OBC":  {"full": "On-Board Computer",             "ja": "搭載計算機",           "category": "onboard"},
    "OBE":  {"full": "On-Board Electronics",          "ja": "搭載電子機器",         "category": "onboard"},
    "MCS":  {"full": "Mission Control System",        "ja": "ミッション管理システム","category": "operations"},
    "LEOP": {"full": "Launch and Early Orbit Phase",  "ja": "打上げ・初期軌道フェーズ","category": "operations"},
}


# ============================================================
# 専門用語辞書: 日本語→英語・関連語・説明
# ============================================================
@dataclass
class SpaceTerm:
    ja: str                        # 日本語名称
    en: str                        # 英語名称
    abbr: str = ""                 # 略語
    category: str = ""             # カテゴリ
    description: str = ""          # 説明
    related: list[str] = field(default_factory=list)  # 関連用語（abbr）
    synonyms_ja: list[str] = field(default_factory=list)  # 日本語の別表記・同義語


SPACE_TERMS: list[SpaceTerm] = [
    # --- 軌道力学 ---
    SpaceTerm("静止軌道", "Geostationary Orbit", "GEO", "orbit",
              "赤道面上高度約35,786kmの円軌道。地球の自転と同期し地上から静止して見える。",
              related=["GTO", "LEO"],
              synonyms_ja=["GEO軌道", "ジオステーショナリー軌道"]),
    SpaceTerm("低軌道", "Low Earth Orbit", "LEO", "orbit",
              "高度200〜2000km程度の軌道。ISS(約400km)などが含まれる。大気抵抗の影響を受ける。",
              related=["GEO", "MEO", "SSO"],
              synonyms_ja=["地球低軌道", "近地球軌道"]),
    SpaceTerm("太陽同期軌道", "Sun-Synchronous Orbit", "SSO", "orbit",
              "軌道面が太陽方向と一定角度を保つ極軌道の一種。地球観測衛星に多用される。",
              related=["LEO"],
              synonyms_ja=["太陽同期極軌道"]),
    SpaceTerm("軌道傾斜角", "Orbital Inclination", "", "orbit",
              "軌道面と赤道面のなす角度（度）。",
              related=["SSO"]),
    SpaceTerm("離心率", "Eccentricity", "e", "orbit",
              "軌道の楕円度を表す無次元数。0=円軌道、0<e<1=楕円軌道、1=放物線軌道。",
              related=[]),

    # --- 熱制御 ---
    SpaceTerm("多層断熱材", "Multi-Layer Insulation", "MLI", "thermal",
              "金属箔と間隔材を交互に積層した断熱材。放射による熱損失を低減する。",
              related=["TCS", "OSR"],
              synonyms_ja=["MLI", "サーマルブランケット", "断熱ブランケット"]),
    SpaceTerm("ヒートパイプ", "Heat Pipe", "", "thermal",
              "作動流体の蒸発・凝縮サイクルで熱を輸送する素子。宇宙機の熱均一化に使用。",
              related=["TCS", "LHP"],
              synonyms_ja=["ヒートパイプ"]),
    SpaceTerm("熱制御系", "Thermal Control System", "TCS", "thermal",
              "宇宙機内の温度を許容範囲に維持するシステム。受動・能動の両手法を組み合わせる。",
              related=["MLI", "OSR", "LHP"],
              synonyms_ja=["サーマル系", "熱制御システム"]),
    SpaceTerm("アルベド", "Albedo", "", "thermal",
              "天体表面の反射率。太陽光の地球アルベド(約0.3)は衛星の熱設計に影響する。",
              related=["TCS"]),

    # --- 構造 ---
    SpaceTerm("有限要素法", "Finite Element Method", "FEM", "structures",
              "構造解析の数値計算手法。複雑な形状の応力・変形を計算できる。",
              related=["FEA", "CLA"],
              synonyms_ja=["FEM解析", "有限要素解析"]),
    SpaceTerm("連成荷重解析", "Coupled Loads Analysis", "CLA", "structures",
              "打上げ時のロケットと衛星の連成振動を解析する手法。設計荷重を決定する。",
              related=["FEM", "FEA"],
              synonyms_ja=["CLA解析"]),
    SpaceTerm("ランダム振動", "Random Vibration", "RV", "structures",
              "打上げ時の音響・空力励振による広帯域振動環境。PSD(パワースペクトル密度)で表現。",
              related=["SV", "AT", "QT"],
              synonyms_ja=["ランダム振動試験"]),

    # --- 電力 ---
    SpaceTerm("太陽電池パドル", "Solar Array", "SAR", "power",
              "太陽光を電力に変換するパドル状の構造。三接合型GaAs太陽電池が主流。",
              related=["EPS", "PCDU"],
              synonyms_ja=["太陽電池アレイ", "SAP", "ソーラーパドル"]),
    SpaceTerm("放電深度", "Depth of Discharge", "DOD", "power",
              "バッテリーの最大容量に対する放電量の割合(%)。DODが低いほどバッテリー寿命が長い。",
              related=["EPS", "SOC"],
              synonyms_ja=["DoD"]),

    # --- 信頼性 ---
    SpaceTerm("故障モード影響解析", "Failure Mode and Effects Analysis", "FMEA", "reliability",
              "各コンポーネントの故障モードを特定し、システムへの影響を体系的に評価する手法。",
              related=["FMECA", "FTA"],
              synonyms_ja=["FMEA解析"]),
    SpaceTerm("フォールトツリー解析", "Fault Tree Analysis", "FTA", "reliability",
              "システム故障の原因を論理的に展開するトップダウン手法。",
              related=["FMEA", "FMECA"],
              synonyms_ja=["FTA解析", "故障木解析"]),

    # --- 推進 ---
    SpaceTerm("比推力", "Specific Impulse", "Isp", "propulsion",
              "推進剤1kgあたりのインパルス(N·s/kg)。推進系の効率指標。値が大きいほど効率的。",
              related=["LH2", "LOX", "MMH"],
              synonyms_ja=["スペシフィックインパルス"]),
    SpaceTerm("デルタV", "Delta-V", "ΔV", "propulsion",
              "軌道変更に必要な速度変化量(m/s)。ミッション設計の基本的な評価指標。",
              related=["Isp", "GNC"],
              synonyms_ja=["ΔV", "速度増分"]),

    # --- 打上げ・ロケット ---
    SpaceTerm("フェアリング", "Fairing", "", "launch",
              "打上げ時にペイロードを保護するロケット先端部のカバー。大気圏通過後に分離。",
              related=["LV"],
              synonyms_ja=["ペイロードフェアリング", "ノーズフェアリング"]),
    SpaceTerm("ステージ分離", "Stage Separation", "", "launch",
              "多段式ロケットの各段が分離する動作。分離衝撃は衛星設計に影響する。",
              related=["LV", "CLA"],
              synonyms_ja=["段間分離"]),

    # --- 地球観測 ---
    SpaceTerm("地上分解能", "Ground Sampling Distance", "GSD", "payload",
              "衛星画像の1ピクセルが対応する地上の距離(m)。小さいほど高分解能。",
              related=["FOV", "IFOV"],
              synonyms_ja=["空間分解能", "GSD"]),
    SpaceTerm("合成開口レーダー", "Synthetic Aperture Radar", "SAR", "payload",
              "移動するセンサーで得た複数のエコーを合成して高分解能画像を生成するレーダー。",
              related=["GSD"],
              synonyms_ja=["SAR"]),
]


# ============================================================
# 用語間の関連グラフ（カテゴリ→関連カテゴリ）
# ============================================================
CATEGORY_RELATIONS: dict[str, list[str]] = {
    "orbit":       ["aocs", "guidance", "propulsion", "operations"],
    "aocs":        ["orbit", "structures", "onboard", "power"],
    "propulsion":  ["aocs", "structures", "orbit"],
    "thermal":     ["structures", "power", "testing"],
    "structures":  ["thermal", "aocs", "testing", "launch"],
    "power":       ["thermal", "aocs", "onboard"],
    "comms":       ["onboard", "operations", "aocs"],
    "onboard":     ["comms", "power", "aocs"],
    "reliability": ["testing", "development", "structures"],
    "testing":     ["reliability", "structures", "thermal"],
    "development": ["reliability", "testing"],
    "launch":      ["structures", "operations"],
    "operations":  ["comms", "onboard", "orbit"],
    "payload":     ["orbit", "structures", "power"],
}


# ============================================================
# API関数
# ============================================================

def expand_abbreviation(abbr: str) -> dict | None:
    """略語を展開する。大文字小文字を無視して検索。"""
    return ABBREVIATIONS.get(abbr.upper()) or ABBREVIATIONS.get(abbr)


def find_terms_by_category(category: str) -> list[SpaceTerm]:
    """カテゴリで用語を絞り込む"""
    return [t for t in SPACE_TERMS if t.category == category]


def search_terms(query: str) -> list[SpaceTerm]:
    """
    クエリにマッチする用語を検索する。
    日本語名、英語名、略語、同義語を全て検索対象とする。
    """
    q_lower = query.lower()
    results = []
    seen = set()

    for term in SPACE_TERMS:
        if term.en in seen:
            continue

        matched = (
            q_lower in term.ja.lower()
            or q_lower in term.en.lower()
            or (term.abbr and q_lower == term.abbr.lower())
            or any(q_lower in s.lower() for s in term.synonyms_ja)
        )

        if matched:
            results.append(term)
            seen.add(term.en)

    # 略語辞書も検索
    for abbr, info in ABBREVIATIONS.items():
        if (
            q_lower in abbr.lower()
            or q_lower in info["full"].lower()
            or q_lower in info["ja"]
        ):
            # SpaceTermに変換して返す（重複除去）
            if info["full"] not in seen:
                results.append(SpaceTerm(
                    ja=info["ja"],
                    en=info["full"],
                    abbr=abbr,
                    category=info.get("category", ""),
                    description=f"{abbr}: {info['full']} ({info['ja']})",
                ))
                seen.add(info["full"])

    return results


def get_related_categories(category: str) -> list[str]:
    """カテゴリに関連するカテゴリ一覧を返す"""
    return CATEGORY_RELATIONS.get(category, [])


def extract_abbreviations_from_text(text: str) -> list[tuple[str, dict]]:
    """
    テキスト中に含まれる略語を全て抽出して展開する。

    日本語テキスト中では \\b 単語境界が機能しないため、
    辞書の全略語を直接照合するアプローチを使用する。

    Returns:
        [(略語, 展開情報), ...] のリスト
    """
    import re
    found = []
    seen = set()

    # 1. 辞書の全略語をテキスト中で直接照合（日本語対応）
    for abbr, info in ABBREVIATIONS.items():
        if abbr in seen:
            continue
        # 英文中では単語境界を使う、日本語混じりでは直接一致
        if re.search(r'(?<![A-Za-z])' + re.escape(abbr) + r'(?![A-Za-z0-9])', text):
            found.append((abbr, info))
            seen.add(abbr)

    return found


def build_context_header(query: str) -> str:
    """
    クエリから関連する専門知識のコンテキストヘッダを生成する。
    RAGコンテキスト注入の前に挿入するプリアンブル。
    """
    lines = ["[宇宙専門知識コンテキスト]"]

    # 略語展開
    abbr_found = extract_abbreviations_from_text(query)
    if abbr_found:
        lines.append("\n【略語展開】")
        for abbr, info in abbr_found:
            lines.append(f"  {abbr} = {info['full']} ({info['ja']})")

    # 関連用語
    terms = search_terms(query)[:3]
    if terms:
        lines.append("\n【関連用語】")
        for t in terms:
            desc = t.description[:80] + "..." if len(t.description) > 80 else t.description
            lines.append(f"  {t.ja} ({t.en}): {desc}")

    return "\n".join(lines) if len(lines) > 1 else ""


if __name__ == "__main__":
    # 動作確認
    print("=== 略語展開テスト ===")
    for abbr in ["LEO", "FMEA", "MLI", "TCS", "GEO"]:
        info = expand_abbreviation(abbr)
        if info:
            print(f"  {abbr} -> {info['full']} ({info['ja']})")

    print("\n=== 用語検索テスト ===")
    for q in ["熱制御", "orbit", "vibration"]:
        results = search_terms(q)
        print(f"  '{q}' -> {[t.ja for t in results[:3]]}")

    print("\n=== コンテキストヘッダ生成テスト ===")
    header = build_context_header("LEO衛星のMLI設計におけるTCS要求を教えて")
    print(header)
