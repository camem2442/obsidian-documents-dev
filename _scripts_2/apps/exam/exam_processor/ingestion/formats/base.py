"""Shared contract for source-format specific ingestion."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FormatRequest:
    problem_path: Path
    solution_path: Path | None
    source_id: str
    track: str
    pages: str
    workdir: Path
    source: str = ""
    structure: dict | None = None
    layout: dict | None = None
    script_path: Path | None = None
    answer_path: Path | None = None


@dataclass
class FormatResult:
    items: list
    warnings: list[str]
    metadata: dict = field(default_factory=dict)


@dataclass
class IngestBatch:
    records: list = field(default_factory=list)
    review_states: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class BaseFormatModule(ABC):
    """Boundary between shared job handling and format-specific parsing."""

    format_id = ""
    label = ""
    extensions = frozenset()
    parser_version = "0.0.0"
    supported_tracks = frozenset()

    def supports(self, path):
        return Path(path).suffix.lower() in self.extensions

    def validate(self, request):
        if not self.supports(request.problem_path):
            raise ValueError(f"{self.label} 형식에서 지원하지 않는 문제 파일입니다.")
        if self.supported_tracks and request.track not in self.supported_tracks:
            raise ValueError(f"{self.label} 형식에서 지원하지 않는 선택과목입니다.")

    @abstractmethod
    def parse(self, request):
        """Return normalized candidate items without writing to the vault."""


@dataclass(frozen=True)
class SolutionFormatRequest:
    path: Path
    source_id: str
    track: str
    workdir: Path
    expected_academic_year: str = ""
    document_id: str = "solution"


class BaseSolutionFormatModule(ABC):
    """Contract for explanation documents that differ from the problem layout."""

    format_id = ""
    label = ""
    parser_version = "0.0.0"

    @abstractmethod
    def supports(self, request):
        """Return whether this module can safely identify the explanation format."""

    @abstractmethod
    def parse(self, request):
        """Return solution candidates and matching metadata."""
