"""
doc_manager - 産業廃棄物処理 文書管理システム

使い方:
    from doc_manager import DocumentManager, DocumentLinker

    dm = DocumentManager()
    doc_id = dm.register_document("マニフェスト", "manifest", "/path/to/file.pdf")

    linker = DocumentLinker()
    linker.link(doc_id, link_type="task", target_id="task-001", relationship="required")
"""

from .document_manager import DocumentManager, DOC_TYPES, VALIDITY_CHECKLISTS
from .document_linker import DocumentLinker, WASTE_PROCEDURE_REQUIREMENTS, RELATIONSHIPS
from .document_reader import read_document, ExtractedDocument
from .version_tracker import (
    extract_version_from_filename,
    compute_text_diff,
    find_latest_version,
    DiffResult,
)
from .db_schema import get_connection, create_tables

__all__ = [
    "DocumentManager",
    "DocumentLinker",
    "read_document",
    "ExtractedDocument",
    "extract_version_from_filename",
    "compute_text_diff",
    "find_latest_version",
    "DiffResult",
    "DOC_TYPES",
    "VALIDITY_CHECKLISTS",
    "WASTE_PROCEDURE_REQUIREMENTS",
    "RELATIONSHIPS",
    "get_connection",
    "create_tables",
]
