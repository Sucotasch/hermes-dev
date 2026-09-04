# -*- coding: utf-8 -*-
"""Deep Research CLI — standalone tool for deep web research.

Usage:
    python deep_research.py "your query here"
    python deep_research.py "your query" --server http://localhost:8888
    python deep_research.py "your query" --validate 100 --output report.md
"""
import argparse
import re
import sys
import os
from datetime import datetime
from pathlib import Path

# Ensure standalone/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from orchestrator import run_deep_research


def slugify(text):
    """Convert text to filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '_', text)
    return text[:60]


def main():
    parser = argparse.ArgumentParser(
        description="Deep Research — deep web search with local LLM synthesis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python deep_research.py "Jacqueline Lovell biography"
  python deep_research.py "Python httpx proxy" --server http://192.168.1.100:8888
  python deep_research.py "modern art trends" --validate 100 --output art_report.md
        """,
    )
    parser.add_argument("query", help="Research query")
    parser.add_argument("--server", default="http://localhost:8888",
                        help="LLM server URL (OpenAI-compatible, default: http://localhost:8888)")
    parser.add_argument("--model", default="local",
                        help="Model name to use (default: local; for Ollama use e.g. ministral-3:14b)")
    parser.add_argument("--query-type", "--qtype", dest="query_type", default=None,
                        metavar="TYPE",
                        help="Set query intent manually, skipping LLM classification: "
                             "person, visual, technical, news, historical, comparison, "
                             "fact, art, education, science, video, general. "
                             "With no LLM server the pipeline runs fully: report is built "
                             "without the synthesis section (no error).")
    parser.add_argument("--validate", type=int, default=50,
                        help="Max URLs to validate (default: 50)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output .md file path (default: auto-generated)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Suppress progress output")

    args = parser.parse_args()

    # Header
    if not args.quiet:
        print("=" * 60)
        print(f"Deep Research: {args.query}")
        print(f"Server: {args.server}"
              + (f" (query_type={args.query_type})" if args.query_type else ""))
        print("=" * 60)

    # Run pipeline
    result = run_deep_research(
        query=args.query,
        server_url=args.server,
        model=args.model,
        max_validate=args.validate,
        verbose=not args.quiet,
        query_type=args.query_type,
    )

    # Output report
    report = result["report"]
    stats = result["stats"]

    if not args.quiet:
        print("\n" + "=" * 60)
        print("REPORT")
        print("=" * 60)
        print(report)

    # Save report (report already includes header, articles, images, synthesis)
    if args.output:
        output_path = Path(args.output)
    else:
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        slug = slugify(args.query)
        date = datetime.now().strftime("%Y-%m-%d")
        output_path = reports_dir / f"{slug}_{date}.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    if not args.quiet:
        print(f"\n{'=' * 60}")
        print(f"Saved to: {output_path}")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
