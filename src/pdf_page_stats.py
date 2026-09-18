from __future__ import annotations

import argparse
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable

from pypdf import PdfReader
from rich.console import Console
from rich.panel import Panel
from rich.table import Table


logging.getLogger("pypdf").setLevel(logging.ERROR)


@dataclass(frozen=True)
class TocSection:
    qualified_name: str
    level: int
    pages: int


@dataclass(frozen=True)
class FilePageCount:
    relative_path: str
    pages: int
    toc_sections: list[TocSection] = field(default_factory=list)


@dataclass(frozen=True)
class PageStats:
    folder: str
    min_pages: int
    max_pages: int
    average_pages: float


@dataclass(frozen=True)
class PdfStatsReport:
    data_dir: Path
    files: list[FilePageCount]
    folder_stats: list[PageStats]
    overall: PageStats | None


@dataclass
class _OutlineNode:
    title: str
    page_index: int | None
    children: list[_OutlineNode] = field(default_factory=list)


def scan_pdf_pages(data_dir: Path | str) -> PdfStatsReport:
    data_path = Path(data_dir).expanduser()
    if not data_path.exists():
        raise FileNotFoundError(f"Data directory does not exist: {data_path}")
    if not data_path.is_dir():
        raise NotADirectoryError(f"Data path is not a directory: {data_path}")

    pdf_paths = sorted(
        (path for path in data_path.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: _relative_name(path, data_path).lower(),
    )

    files = []
    for path in pdf_paths:
        relative_name = _relative_name(path, data_path)
        try:
            files.append(_scan_pdf(path, data_path))
        except Exception as error:
            raise RuntimeError(f"Could not read PDF {relative_name}: {error}") from error
    folder_stats = _folder_stats(files)
    overall = _stats_for_pages("All PDFs", [file.pages for file in files]) if files else None
    return PdfStatsReport(data_dir=data_path, files=files, folder_stats=folder_stats, overall=overall)


def count_pdf_pages(path: Path) -> int:
    reader = _open_pdf(path)
    return len(reader.pages)


def render_report(report: PdfStatsReport, console: Console | None = None) -> None:
    output = console or Console()
    output.print(Panel.fit(f"[bold]PDF page statistics[/bold]\n{report.data_dir}", border_style="cyan"))

    if not report.files:
        output.print("[yellow]No PDF files found.[/yellow]")
        return

    output.print(_files_table(report.files))
    output.print(_folders_table(report.folder_stats))
    output.print(_overall_table(report.overall, len(report.files)))
    for file in report.files:
        if file.toc_sections:
            output.print(_toc_table(file))
        else:
            output.print(f"{file.relative_path} has no ToC metadata")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute page-count statistics for PDFs.")
    parser.add_argument(
        "data_dir",
        nargs="?",
        default="data",
        help="Directory containing PDFs. Defaults to ./data.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("pdf_page_stats.txt"),
        help="File to write the plain-text report to. Defaults to ./pdf_page_stats.txt.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    console = Console(record=True)
    try:
        report = scan_pdf_pages(args.data_dir)
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as error:
        console.print(f"[bold red]Error:[/bold red] {error}")
        return 1

    render_report(report, console)
    args.output.write_text(console.export_text(), encoding="utf-8")
    return 0


def _scan_pdf(path: Path, data_path: Path) -> FilePageCount:
    reader = _open_pdf(path)
    page_count = len(reader.pages)
    return FilePageCount(
        relative_path=_relative_name(path, data_path),
        pages=page_count,
        toc_sections=_toc_sections(reader, page_count),
    )


def _open_pdf(path: Path) -> PdfReader:
    reader = PdfReader(str(path))
    if reader.is_encrypted:
        reader.decrypt("")
    return reader


def _toc_sections(reader: PdfReader, page_count: int) -> list[TocSection]:
    try:
        outline = reader.outline
    except Exception:
        return []
    if not outline:
        return []

    outline_items = outline if isinstance(outline, list) else [outline]
    nodes = _parse_outline(outline_items, reader, page_count)
    return _section_spans(nodes, parent_names=[], level=1, parent_end=page_count)


def _parse_outline(items: list[Any], reader: PdfReader, page_count: int) -> list[_OutlineNode]:
    nodes: list[_OutlineNode] = []
    previous_node: _OutlineNode | None = None

    for item in items:
        if isinstance(item, list):
            if previous_node is not None:
                previous_node.children.extend(_parse_outline(item, reader, page_count))
            continue

        node = _OutlineNode(
            title=_normalize_title(getattr(item, "title", str(item))),
            page_index=_destination_page_index(reader, item, page_count),
        )
        nodes.append(node)
        previous_node = node

    return nodes


def _section_spans(
    nodes: list[_OutlineNode],
    parent_names: list[str],
    level: int,
    parent_end: int,
) -> list[TocSection]:
    sections: list[TocSection] = []
    starts = [_first_start_page(node) for node in nodes]

    for index, node in enumerate(nodes):
        start_page = starts[index]
        if start_page is None:
            continue

        end_page = _next_sibling_start(starts, index) or parent_end
        end_page = max(start_page, end_page)
        qualified_parts = [*parent_names, node.title]
        qualified_name = " / ".join(qualified_parts)
        sections.append(TocSection(qualified_name=qualified_name, level=level, pages=end_page - start_page))
        sections.extend(_section_spans(node.children, qualified_parts, level + 1, end_page))

    return sections


def _first_start_page(node: _OutlineNode) -> int | None:
    if node.page_index is not None:
        return node.page_index
    for child in node.children:
        child_start = _first_start_page(child)
        if child_start is not None:
            return child_start
    return None


def _next_sibling_start(starts: list[int | None], current_index: int) -> int | None:
    for start_page in starts[current_index + 1 :]:
        if start_page is not None:
            return start_page
    return None


def _destination_page_index(reader: PdfReader, item: Any, page_count: int) -> int | None:
    try:
        page_index = reader.get_destination_page_number(item)
    except Exception:
        return None
    if isinstance(page_index, bool) or not isinstance(page_index, int):
        return None
    if page_index < 0 or page_index >= page_count:
        return None
    return page_index


def _normalize_title(title: str) -> str:
    return " ".join(str(title).split()) or "Untitled section"


def _folder_stats(files: list[FilePageCount]) -> list[PageStats]:
    pages_by_folder: dict[str, list[int]] = defaultdict(list)
    for file in files:
        folder = Path(file.relative_path).parent.as_posix()
        pages_by_folder["data root" if folder == "." else folder].append(file.pages)

    return [
        _stats_for_pages(folder, pages)
        for folder, pages in sorted(pages_by_folder.items(), key=lambda item: item[0].lower())
    ]


def _stats_for_pages(folder: str, pages: list[int]) -> PageStats:
    return PageStats(
        folder=folder,
        min_pages=min(pages),
        max_pages=max(pages),
        average_pages=fmean(pages),
    )


def _relative_name(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def _format_average(value: float) -> str:
    return f"{value:.2f}"


def _files_table(files: list[FilePageCount]) -> Table:
    table = Table(title="Pages per PDF", show_lines=False)
    table.add_column("File", overflow="fold")
    table.add_column("Pages", justify="right")
    for file in files:
        table.add_row(file.relative_path, str(file.pages))
    return table


def _folders_table(folder_stats: list[PageStats]) -> Table:
    table = Table(title="Subfolder Page Stats", show_lines=False)
    table.add_column("Folder", overflow="fold")
    table.add_column("Min pages", justify="right")
    table.add_column("Max pages", justify="right")
    table.add_column("Average pages", justify="right")
    for stats in folder_stats:
        table.add_row(
            stats.folder,
            str(stats.min_pages),
            str(stats.max_pages),
            _format_average(stats.average_pages),
        )
    return table


def _overall_table(overall: PageStats | None, file_count: int) -> Table:
    table = Table(title="Overall Page Stats", show_lines=False)
    table.add_column("Scope")
    table.add_column("Files", justify="right")
    table.add_column("Min pages", justify="right")
    table.add_column("Max pages", justify="right")
    table.add_column("Average pages", justify="right")
    if overall is not None:
        table.add_row(
            overall.folder,
            str(file_count),
            str(overall.min_pages),
            str(overall.max_pages),
            _format_average(overall.average_pages),
        )
    return table


def _toc_table(file: FilePageCount) -> Table:
    table = Table(title=f"ToC sections: {file.relative_path}", show_lines=False)
    table.add_column("Level", justify="right")
    table.add_column("Section", overflow="fold")
    table.add_column("Pages", justify="right")

    for section in file.toc_sections:
        table.add_row(str(section.level), section.qualified_name, str(section.pages))

    return table


if __name__ == "__main__":
    raise SystemExit(main())
