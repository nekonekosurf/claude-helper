"""
宇宙分野データ収集スクリプト

NASA NTRS、JAXA リポジトリ、arXiv から文書を自動収集する。

実行方法:
    # NASA NTRS からダウンロード
    uv run python -m space_rag.data_collector nasa --query "thermal control satellite" --max 10

    # arXiv から収集
    uv run python -m space_rag.data_collector arxiv --category astro-ph --max 20

    # JAXA 公開文書リストを表示
    uv run python -m space_rag.data_collector jaxa --list

    # 全ソースを一括収集
    uv run python -m space_rag.data_collector all

注意:
    - 収集は公開APIのみを使用（認証不要）
    - レート制限を遵守するため、リクエスト間に待機時間を設ける
    - ダウンロード済みファイルはスキップ（冪等性確保）
"""

from __future__ import annotations

import json
import time
import logging
import sys
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from dataclasses import dataclass

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
SPACE_DOCS_DIR = DATA_DIR / "space_docs"


@dataclass
class DocumentRecord:
    """収集した文書のメタデータ"""
    doc_id: str
    title: str
    source: str      # nasa_ntrs / jaxa / esa / arxiv
    url: str
    local_path: str
    category: str = ""
    abstract: str = ""
    authors: list[str] = None
    year: int = 0

    def __post_init__(self):
        if self.authors is None:
            self.authors = []


# ============================================================
# NASA Technical Reports Server (NTRS)
# ============================================================

class NASANTRSCollector:
    """
    NASA Technical Reports Server (NTRS) から技術報告書を収集する。

    API: https://ntrs.nasa.gov/api/citations/search
    ライセンス: パブリックドメイン（米国政府著作物）

    宇宙関連の有用な検索クエリ例:
    - "spacecraft thermal control"
    - "attitude control system small satellite"
    - "electric propulsion cubesat"
    - "structural analysis launch vehicle"
    """

    BASE_URL = "https://ntrs.nasa.gov/api/citations/search"
    DOWNLOAD_URL = "https://ntrs.nasa.gov/api/citations/{id}/downloads"

    # 宇宙分野の推奨検索クエリ（category → queries）
    DEFAULT_QUERIES = {
        "thermal": [
            "spacecraft thermal control design",
            "multi layer insulation satellite",
            "heat pipe spacecraft",
        ],
        "aocs": [
            "attitude determination control small satellite",
            "reaction wheel control system",
            "star tracker attitude determination",
        ],
        "structures": [
            "spacecraft structural analysis launch loads",
            "composite structure satellite",
        ],
        "propulsion": [
            "electric propulsion spacecraft",
            "ion thruster small satellite",
        ],
        "power": [
            "solar array power system satellite",
            "battery management spacecraft",
        ],
        "comms": [
            "telemetry command downlink satellite",
            "antenna design spacecraft communication",
        ],
    }

    def __init__(self, output_dir: Path | None = None, rate_limit_sec: float = 1.0):
        self.output_dir = (output_dir or SPACE_DOCS_DIR) / "nasa"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limit = rate_limit_sec
        self._metadata_path = self.output_dir / "_metadata.jsonl"

    def search(
        self,
        query: str,
        max_results: int = 10,
    ) -> list[dict]:
        """
        NTRSで文書を検索する。

        Returns:
            APIレスポンスの結果リスト（raw JSON）
        """
        params = {
            "q": query,
            "rows": min(max_results, 25),  # NTRS最大25件
            "start": 0,
        }
        url = self.BASE_URL + "?" + urllib.parse.urlencode(params)
        logger.info(f"NTRS search: {query!r}")

        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "SpaceRAG/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                return data.get("results", [])
        except urllib.error.URLError as e:
            logger.error(f"NTRS search failed: {e}")
            return []

    def download(
        self,
        doc_id: str,
        title: str,
        max_retries: int = 2,
    ) -> Path | None:
        """
        NTRSから文書PDFをダウンロードする。

        Args:
            doc_id: NTRS文書ID
            title: 文書タイトル（ファイル名に使用）

        Returns:
            保存先のPathオブジェクト、失敗時はNone
        """
        # ファイル名をサニタイズ
        safe_title = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title[:60])
        output_path = self.output_dir / f"NTRS_{doc_id}_{safe_title}.pdf"

        if output_path.exists():
            logger.debug(f"Already downloaded: {output_path.name}")
            return output_path

        # ダウンロードURL取得
        url = self.DOWNLOAD_URL.format(id=doc_id)
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "SpaceRAG/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                downloads = json.loads(resp.read())

            if not downloads:
                logger.warning(f"No downloads available for NTRS:{doc_id}")
                return None

            # PDFを優先してダウンロード
            pdf_url = None
            for dl in downloads:
                if dl.get("mimeType") == "application/pdf":
                    pdf_url = dl.get("links", {}).get("pdf") or dl.get("uri", "")
                    break

            if not pdf_url:
                logger.warning(f"No PDF found for NTRS:{doc_id}")
                return None

            logger.info(f"Downloading: {output_path.name}")
            req = urllib.request.Request(
                pdf_url,
                headers={"User-Agent": "SpaceRAG/1.0"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                output_path.write_bytes(resp.read())

            time.sleep(self.rate_limit)
            return output_path

        except Exception as e:
            logger.error(f"Download failed for NTRS:{doc_id}: {e}")
            return None

    def collect(
        self,
        queries: list[str] | None = None,
        categories: list[str] | None = None,
        max_per_query: int = 5,
        download_pdfs: bool = True,
    ) -> list[DocumentRecord]:
        """
        一括収集を実行する。

        Args:
            queries: 検索クエリリスト（Noneの場合はデフォルトクエリを使用）
            categories: 収集するカテゴリリスト（Noneの場合は全カテゴリ）
            max_per_query: クエリあたりの最大取得件数
            download_pdfs: PDFをダウンロードするか（Falseの場合はメタデータのみ）
        """
        records = []
        loaded_metadata = self._load_metadata()

        if queries is None:
            # カテゴリフィルタ
            target_cats = categories or list(self.DEFAULT_QUERIES.keys())
            queries = []
            cat_map = []
            for cat in target_cats:
                for q in self.DEFAULT_QUERIES.get(cat, []):
                    queries.append(q)
                    cat_map.append(cat)
        else:
            cat_map = ["general"] * len(queries)

        for query, category in zip(queries, cat_map):
            results = self.search(query, max_results=max_per_query)
            time.sleep(self.rate_limit)

            for item in results:
                doc_id = str(item.get("id", ""))
                if not doc_id or doc_id in loaded_metadata:
                    continue

                title = item.get("title", "Untitled")
                abstract = item.get("abstract", "")

                local_path = ""
                if download_pdfs:
                    path = self.download(doc_id, title)
                    local_path = str(path) if path else ""
                    time.sleep(self.rate_limit)

                record = DocumentRecord(
                    doc_id=f"NTRS_{doc_id}",
                    title=title,
                    source="nasa_ntrs",
                    url=f"https://ntrs.nasa.gov/citations/{doc_id}",
                    local_path=local_path,
                    category=category,
                    abstract=abstract,
                    year=int(str(item.get("stiTypeDetails", {}).get("year", 0))[:4] or "0"),
                )
                records.append(record)
                self._save_metadata(record)

        logger.info(f"NTRS: collected {len(records)} new documents")
        return records

    def _load_metadata(self) -> set[str]:
        """既存メタデータのdoc_idセットを読み込む"""
        seen = set()
        if self._metadata_path.exists():
            with open(self._metadata_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        seen.add(data.get("doc_id", ""))
                    except Exception:
                        pass
        return seen

    def _save_metadata(self, record: DocumentRecord):
        """メタデータをJSONLに追記する"""
        with open(self._metadata_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "doc_id": record.doc_id,
                "title": record.title,
                "url": record.url,
                "category": record.category,
                "abstract": record.abstract[:300],
            }, ensure_ascii=False) + "\n")


# ============================================================
# arXiv Collector
# ============================================================

class ArXivCollector:
    """
    arXiv から宇宙分野の論文を収集する。

    関連カテゴリ:
    - astro-ph.IM: Instrumentation and Methods
    - astro-ph.EP: Earth and Planetary Astrophysics
    - astro-ph.SR: Solar and Stellar Astrophysics
    - cs.RO: Robotics（宇宙ロボット）
    - eess.SP: Signal Processing
    """

    BASE_URL = "http://export.arxiv.org/api/query"

    # 宇宙工学に関連するarXivの推奨検索語
    SPACE_ENGINEERING_QUERIES = [
        "CubeSat attitude control",
        "satellite thermal management",
        "spacecraft power system",
        "electric propulsion smallsat",
        "debris mitigation spacecraft design",
        "on-orbit servicing autonomous",
        "constellation communication satellite",
    ]

    def __init__(self, output_dir: Path | None = None, rate_limit_sec: float = 3.0):
        self.output_dir = (output_dir or SPACE_DOCS_DIR) / "arxiv"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limit = rate_limit_sec  # arXivは3秒以上

    def search_and_collect(
        self,
        query: str,
        category: str = "astro-ph",
        max_results: int = 10,
        download_pdfs: bool = False,  # arXivはデフォルトでメタデータのみ
    ) -> list[DocumentRecord]:
        """
        arXiv APIで検索してメタデータを収集する。

        Note: PDFダウンロードはarXivのポリシーにより控えめに。
              abstract取得はOKだがPDF大量DLは避ける。
        """
        params = {
            "search_query": f"cat:{category} AND all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
        }
        url = self.BASE_URL + "?" + urllib.parse.urlencode(params)
        logger.info(f"arXiv search: {query!r} in {category}")

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SpaceRAG/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml_data = resp.read().decode("utf-8")
        except urllib.error.URLError as e:
            logger.error(f"arXiv search failed: {e}")
            return []

        records = self._parse_atom_feed(xml_data, download_pdfs)
        time.sleep(self.rate_limit)
        return records

    def _parse_atom_feed(self, xml: str, download_pdfs: bool) -> list[DocumentRecord]:
        """ArXiv AtomフィードをパースしてDocumentRecordリストを返す"""
        import xml.etree.ElementTree as ET

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }

        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            logger.error(f"XML parse failed: {e}")
            return []

        records = []
        for entry in root.findall("atom:entry", ns):
            arxiv_id = (entry.findtext("atom:id", "", ns) or "").split("/")[-1]
            title = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
            abstract = (entry.findtext("atom:summary", "", ns) or "").strip()

            # abstract のみでチャンクを生成（PDF不要）
            # タイトル + abstract をテキストとして使用
            text = f"Title: {title}\n\nAbstract: {abstract}"

            safe_id = arxiv_id.replace("/", "_").replace(".", "_")
            local_path = ""

            if download_pdfs:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
                out_path = self.output_dir / f"arxiv_{safe_id}.pdf"
                if not out_path.exists():
                    try:
                        req = urllib.request.Request(
                            pdf_url, headers={"User-Agent": "SpaceRAG/1.0"}
                        )
                        with urllib.request.urlopen(req, timeout=60) as resp:
                            out_path.write_bytes(resp.read())
                        local_path = str(out_path)
                        time.sleep(self.rate_limit * 2)  # PDF DLは長めに待機
                    except Exception as e:
                        logger.warning(f"PDF download failed: {e}")
                else:
                    local_path = str(out_path)

            record = DocumentRecord(
                doc_id=f"arxiv_{safe_id}",
                title=title,
                source="arxiv",
                url=f"https://arxiv.org/abs/{arxiv_id}",
                local_path=local_path,
                abstract=abstract,
            )
            records.append(record)

        return records


# ============================================================
# JAXA 公開文書リスト（URLは手動管理）
# ============================================================

# JAXA公開技術文書の既知URL一覧
# （JAXAのAPIは非公開のため、公開されているJERG等を手動管理）
JAXA_PUBLIC_DOCS: list[dict] = [
    {
        "doc_id": "JERG-2-210",
        "title": "宇宙機熱制御システム設計標準",
        "url": "https://repository.exst.jaxa.jp/dspace/handle/123456789/37671",
        "category": "thermal",
        "source": "jaxa",
    },
    {
        "doc_id": "JERG-2-311",
        "title": "宇宙機機械構造設計標準",
        "url": "https://repository.exst.jaxa.jp/dspace/handle/123456789/37664",
        "category": "structures",
        "source": "jaxa",
    },
    # 実際には JAXAのJERGリスト全体を追加する
    # 注意: ダウンロード前に利用規約を確認すること
]


class JAXACollector:
    """
    JAXA公開文書を収集する。

    注意: JAXAのJERG文書は原則公開されているが、
          利用規約（著作権）を確認の上ダウンロードすること。
    """

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = (output_dir or SPACE_DOCS_DIR) / "jaxa"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def list_known_docs(self) -> list[dict]:
        """既知のJAXA公開文書一覧を返す"""
        return JAXA_PUBLIC_DOCS

    def print_list(self):
        """文書リストを表示する"""
        print("=== JAXA 公開文書リスト ===")
        for doc in JAXA_PUBLIC_DOCS:
            print(f"  [{doc['doc_id']}] {doc['title']}")
            print(f"    URL: {doc['url']}")
            print(f"    category: {doc['category']}")
            print()
        print(f"Total: {len(JAXA_PUBLIC_DOCS)} documents")
        print()
        print("ダウンロード方法:")
        print("  ブラウザから手動でダウンロードして data/space_docs/jaxa/ に保存してください。")
        print("  その後: uv run python -m space_rag.knowledge_builder build")


# ============================================================
# CLI
# ============================================================

def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "nasa":
        # NASA NTRS から収集
        query = None
        max_results = 5
        download = False

        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--query" and i + 1 < len(sys.argv):
                query = sys.argv[i + 1]
            elif arg == "--max" and i + 1 < len(sys.argv):
                max_results = int(sys.argv[i + 1])
            elif arg == "--download":
                download = True

        collector = NASANTRSCollector()
        if query:
            records = collector.collect(
                queries=[query],
                max_per_query=max_results,
                download_pdfs=download,
            )
        else:
            # デフォルトクエリで収集
            records = collector.collect(
                max_per_query=max_results,
                download_pdfs=download,
            )

        print(f"NASA NTRS: {len(records)} documents collected")
        for r in records[:5]:
            print(f"  [{r.doc_id}] {r.title[:60]}...")

    elif command == "arxiv":
        # arXiv から収集
        query = "small satellite design"
        category = "astro-ph"
        max_results = 5

        for i, arg in enumerate(sys.argv[2:], 2):
            if arg == "--query" and i + 1 < len(sys.argv):
                query = sys.argv[i + 1]
            elif arg == "--category" and i + 1 < len(sys.argv):
                category = sys.argv[i + 1]
            elif arg == "--max" and i + 1 < len(sys.argv):
                max_results = int(sys.argv[i + 1])

        collector = ArXivCollector()
        records = collector.search_and_collect(query, category, max_results)
        print(f"arXiv: {len(records)} papers collected")
        for r in records[:5]:
            print(f"  [{r.doc_id}] {r.title[:60]}...")

    elif command == "jaxa":
        # JAXA 文書リスト表示
        JAXACollector().print_list()

    elif command == "all":
        # 全ソースを一括収集（メタデータのみ、ダウンロードなし）
        print("=== NASA NTRS 収集 ===")
        nasa = NASANTRSCollector()
        nasa_records = nasa.collect(max_per_query=3, download_pdfs=False)
        print(f"  NASA: {len(nasa_records)} documents")

        print("\n=== arXiv 収集 ===")
        arxiv = ArXivCollector()
        for q in ArXivCollector.SPACE_ENGINEERING_QUERIES[:3]:
            records = arxiv.search_and_collect(q, max_results=3)
            print(f"  '{q}': {len(records)} papers")

        print("\n=== JAXA 文書 ===")
        JAXACollector().print_list()

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
