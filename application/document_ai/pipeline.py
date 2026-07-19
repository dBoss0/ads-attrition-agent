"""
Document AI Pipeline — full protocol → ParsedProtocol orchestration.

Flow:
  1. Upload file to Volume via VolumeFileStore
  2. DatabricksDocumentParser.parse_from_volume()  ← ai_parse_document()
  3. SectionClassifier.classify()                  ← ai_classify()
  4. DataSourceDetector.detect()                   ← full text, parser.py rules
  5. CriteriaExtractor.extract()                   ← ai_extract() + Claude Opus 4.8
  6. Assemble ParsedProtocol

Human-in-the-Loop gate: pipeline returns ParsedProtocol with state EXTRACTION_COMPLETE.
The analyst reviews, edits, and approves before the session advances.

All five Databricks AI calls (ai_parse_document, ai_classify, ai_extract) happen
inside this pipeline. Each has a keyword fallback for local dev / testing.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from application.document_ai.databricks_parser import DatabricksDocumentParser
from application.document_ai.section_classifier import SectionClassifier
from application.document_ai.criteria_extractor import CriteriaExtractor
from application.document_ai.data_source_detector import DataSourceDetector
from domain.entities.protocol import FileType, ParsedProtocol
from infrastructure.volume.file_store import VolumeFileStore

if TYPE_CHECKING:
    from pyspark.sql import SparkSession
    from infrastructure.llm.router import LLMRouter
    from application.metadata.context_provider import MetadataContextProvider

logger = logging.getLogger(__name__)


class DocumentAIPipeline:
    """
    Orchestrates the full document → ParsedProtocol pipeline.
    Wires together all Document AI components.
    """

    def __init__(
        self,
        router: "LLMRouter",
        spark: "SparkSession | None" = None,
        metadata_provider: "MetadataContextProvider | None" = None,
    ) -> None:
        self._router = router
        self._spark = spark
        self._metadata_provider = metadata_provider
        self._doc_parser = DatabricksDocumentParser(spark)
        self._classifier = SectionClassifier(spark)
        self._extractor = CriteriaExtractor(router, spark)
        self._detector = DataSourceDetector()
        self._volume = VolumeFileStore()

    # ── Entry points ───────────────────────────────────────────────────────────

    def process_upload(
        self,
        content: bytes,
        filename: str,
        session_id: str,
        analyst_email: str = "",
    ) -> ParsedProtocol:
        """
        Full pipeline from uploaded bytes → ParsedProtocol.
        Called by the Gradio upload handler.
        """
        # Step 1: Store to Volume
        volume_path = self._volume.upload_protocol(filename, content)
        logger.info("Protocol stored: %s → %s", filename, volume_path)

        # Step 2: Parse document
        doc = self._doc_parser.parse_from_volume(volume_path)
        if not doc.text:
            logger.error("Document parser returned empty text for: %s", filename)
            return _empty_protocol(filename)

        return self._run_pipeline(
            full_text=doc.text,
            pages=doc.pages,
            filename=filename,
            session_id=session_id,
            parser_used=doc.parser_used,
        )

    def process_volume_path(
        self,
        volume_path: str,
        session_id: str,
    ) -> ParsedProtocol:
        """Process a file already in the Volume (admin re-process scenario)."""
        doc = self._doc_parser.parse_from_volume(volume_path)
        if not doc.text:
            return _empty_protocol(volume_path)
        return self._run_pipeline(
            full_text=doc.text,
            pages=doc.pages,
            filename=Path(volume_path).name,
            session_id=session_id,
            parser_used=doc.parser_used,
        )

    # ── Core pipeline ──────────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        full_text: str,
        pages: list[str],
        filename: str,
        session_id: str,
        parser_used: str,
    ) -> ParsedProtocol:
        """
        Steps 3–6: classify → detect data sources → extract criteria → assemble.
        """
        # Step 3: Section classification (ai_classify)
        chunks = pages if pages else _chunk_text(full_text)
        sections = self._classifier.classify(chunks, session_id=session_id)
        logger.info(
            "Classified %d sections: %s",
            len(sections),
            [s.section_type for s in sections],
        )

        # Step 4: Data source detection (parser.py rules on full text)
        data_sources = self._detector.detect(full_text)
        logger.info("Detected data sources: %s", data_sources)

        # Step 5: Criteria extraction (ai_extract + Claude)
        study_design_text = next(
            (s.text for s in sections
             if s.section_type.value == "study_design"),
            "",
        )
        study_population_text = next(
            (s.text for s in sections
             if s.section_type.value == "study_population"),
            "",
        )

        # Build minimal metadata context for the extraction prompt
        meta_context = None
        if self._metadata_provider and data_sources:
            try:
                meta_context = self._metadata_provider.build_context(
                    criterion_text="study eligibility criteria",
                    clinical_concept="other",
                )
            except Exception as exc:
                logger.warning("Metadata context unavailable: %s", exc)

        inclusions, exclusions = self._extractor.extract(
            sections=sections,
            session_id=session_id,
            metadata_context=meta_context,
        )

        logger.info(
            "Extraction complete — %d inclusion, %d exclusion criteria",
            len(inclusions), len(exclusions),
        )

        # Step 6: Assemble ParsedProtocol
        return ParsedProtocol(
            source_filename=filename,
            file_type=_detect_file_type(filename),
            study_design=study_design_text,
            study_population=study_population_text,
            inclusion_criteria=inclusions,
            exclusion_criteria=exclusions,
            data_sources=data_sources,
            extracted_sections=sections,
            extraction_model=f"{parser_used}+ai_classify+ai_extract+claude-opus-4-8",
            raw_text_length=len(full_text),
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _detect_file_type(filename: str) -> FileType:
    ext = Path(filename).suffix.lower()
    return {".docx": FileType.DOCX, ".pdf": FileType.PDF, ".xlsx": FileType.EXCEL}.get(
        ext, FileType.UNKNOWN
    )


def _chunk_text(text: str, chunk_size: int = 2000) -> list[str]:
    """Split full text into paragraphs for classification."""
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 20]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        if current_len + len(para) > chunk_size and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))
    return chunks or [text[:chunk_size]]


def _empty_protocol(filename: str) -> ParsedProtocol:
    return ParsedProtocol(
        source_filename=filename,
        file_type=_detect_file_type(filename),
        extraction_model="failed",
    )
