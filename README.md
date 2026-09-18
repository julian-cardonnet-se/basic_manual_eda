# Manuals PDF Exploration

A small Python playground for exploring PDF collections. The current utility scans a directory recursively, counts pages, summarizes page counts by folder, and reports table-of-contents section spans when PDF outline metadata is available.

## Requirements

Dependencies are declared in `pyproject.toml` and locked in `uv.lock`.

## Setup

From the project root:

```text
uv sync
```

Place PDF files in `data/`, or provide another directory when running the utility.

## PDF page statistics

Run the default scan:

```text
uv run python scr/pdf_page_stats.py
```

The command prints a Rich-formatted report and writes a plain-text copy to `pdf_page_stats.txt`.

To scan another directory and choose the output file:

```text
uv run python scr/pdf_page_stats.py path/to/pdfs --output path/to/report.txt
```

The report includes:

## Tests

Run the focused test suite with:

```text
uv run python -m unittest discover -s scr -p 'test_pdf_page_stats.py' -v
```

## Project layout

```text
data/                  PDF input files
docs/                  Project documentation
scr/pdf_page_stats.py  PDF scanning utility
scr/test_pdf_page_stats.py
src/                   Space for additional source modules
```
