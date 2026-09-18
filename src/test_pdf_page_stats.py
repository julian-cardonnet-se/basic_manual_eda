import io
import math
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfWriter
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pdf_page_stats import main, render_report, scan_pdf_pages


def write_pdf(path: Path, page_count: int) -> None:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            b"<< /Type /Pages /Kids ["
            + b" ".join(f"{index} 0 R".encode() for index in range(3, page_count + 3))
            + f"] /Count {page_count} >>".encode()
        ),
    ]
    objects.extend(
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] /Resources << >> >>"
        for _ in range(page_count)
    )

    content = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{object_number} 0 obj\n".encode())
        content.extend(body)
        content.extend(b"\nendobj\n")

    xref_offset = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode())
    content.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def write_pdf_with_outline(path: Path) -> None:
    writer = PdfWriter()
    for _ in range(10):
        writer.add_blank_page(width=72, height=72)

    part_a = writer.add_outline_item("  Part   A  ", 1)
    writer.add_outline_item("Chapter 1", 1, parent=part_a)
    writer.add_outline_item("Chapter 2", 4, parent=part_a)
    part_b = writer.add_outline_item("Part B", 7)
    writer.add_outline_item("Chapter 3", 8, parent=part_b)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        writer.write(output)


class PdfPageStatsTest(unittest.TestCase):
    def test_scan_pdf_pages_counts_files_and_folder_stats_recursively(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            write_pdf(data_dir / "Alpha" / "one.pdf", 1)
            write_pdf(data_dir / "Alpha" / "five.PDF", 5)
            write_pdf(data_dir / "Beta" / "Nested" / "three.pdf", 3)
            (data_dir / "Alpha" / "notes.txt").write_text("not a pdf")

            report = scan_pdf_pages(data_dir)

        self.assertEqual(
            [(file.relative_path, file.pages) for file in report.files],
            [
                ("Alpha/five.PDF", 5),
                ("Alpha/one.pdf", 1),
                ("Beta/Nested/three.pdf", 3),
            ],
        )

        folder_stats = {stats.folder: stats for stats in report.folder_stats}
        self.assertEqual(folder_stats["Alpha"].min_pages, 1)
        self.assertEqual(folder_stats["Alpha"].max_pages, 5)
        self.assertTrue(math.isclose(folder_stats["Alpha"].average_pages, 3.0))
        self.assertEqual(folder_stats["Beta/Nested"].min_pages, 3)
        self.assertEqual(folder_stats["Beta/Nested"].max_pages, 3)
        self.assertTrue(math.isclose(folder_stats["Beta/Nested"].average_pages, 3.0))

        self.assertEqual(report.overall.min_pages, 1)
        self.assertEqual(report.overall.max_pages, 5)
        self.assertTrue(math.isclose(report.overall.average_pages, 3.0))

    def test_scan_pdf_pages_computes_toc_section_spans_hierarchically(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            write_pdf_with_outline(data_dir / "manual.pdf")

            report = scan_pdf_pages(data_dir)

        self.assertEqual(len(report.files), 1)
        self.assertEqual(
            [(section.qualified_name, section.level, section.pages) for section in report.files[0].toc_sections],
            [
                ("Part A", 1, 6),
                ("Part A / Chapter 1", 2, 3),
                ("Part A / Chapter 2", 2, 3),
                ("Part B", 1, 3),
                ("Part B / Chapter 3", 2, 2),
            ],
        )

    def test_scan_pdf_pages_marks_pdf_without_outline_as_no_toc_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            write_pdf(data_dir / "plain.pdf", 3)

            report = scan_pdf_pages(data_dir)

        self.assertEqual(len(report.files), 1)
        self.assertEqual(report.files[0].toc_sections, [])

    def test_render_report_prints_no_toc_metadata_message(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            write_pdf(data_dir / "plain.pdf", 3)

            report = scan_pdf_pages(data_dir)

        console = Console(record=True, file=io.StringIO(), width=120, color_system=None)
        render_report(report, console)

        rendered_lines = [line.strip() for line in console.export_text().splitlines()]
        self.assertIn("plain.pdf has no ToC metadata", rendered_lines)

    def test_render_report_prints_toc_table_per_pdf(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            write_pdf_with_outline(data_dir / "manual.pdf")

            report = scan_pdf_pages(data_dir)

        console = Console(record=True, file=io.StringIO(), width=120, color_system=None)
        render_report(report, console)

        rendered = console.export_text()
        self.assertIn("ToC sections: manual.pdf", rendered)
        self.assertIn("Part A / Chapter 1", rendered)

    def test_main_writes_report_to_output_file(self) -> None:
        with TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            output_path = Path(temp_dir) / "report.txt"
            write_pdf(data_dir / "plain.pdf", 3)

            exit_code = main([str(data_dir), "--output", str(output_path)])

            self.assertEqual(exit_code, 0)
            report_text = output_path.read_text(encoding="utf-8")

        self.assertIn("PDF page statistics", report_text)
        self.assertIn("plain.pdf", report_text)
        self.assertIn("plain.pdf has no ToC metadata", report_text)

    def test_scan_pdf_pages_reads_project_data_directory(self) -> None:
        data_dir = Path(__file__).resolve().parents[1] / "data"

        report = scan_pdf_pages(data_dir)

        self.assertGreater(len(report.files), 0)
        self.assertIsNotNone(report.overall)
        self.assertTrue(all(hasattr(file, "toc_sections") for file in report.files))


if __name__ == "__main__":
    unittest.main()
