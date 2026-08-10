"""Pipeline test: run deep_research with different settings, collect logs."""
import sys
import os
import time
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "standalone"))

from orchestrator import run_deep_research

QUERY = "Sara St James free image gallery, Sara St James forum, Sara St James fansite"
SERVER = "http://127.0.0.1:8888"

TESTS = [
    # (name, params)
    ("baseline", dict(
        max_validate=50, top_n=10, images_count=10, llm_sources=10,
        max_variants=4, max_imgs_per_page=5
    )),
    ("deep视觉", dict(
        max_validate=100, top_n=20, images_count=20, llm_sources=15,
        max_variants=6, max_imgs_per_page=50
    )),
    ("all_images", dict(
        max_validate=100, top_n=20, images_count=0, llm_sources=10,
        max_variants=6, max_imgs_per_page=0
    )),
    ("minimal", dict(
        max_validate=30, top_n=5, images_count=5, llm_sources=5,
        max_variants=2, max_imgs_per_page=3
    )),
]

results = []

for name, params in TESTS:
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"Params: {json.dumps(params, indent=2)}")
    print(f"{'='*60}")

    log_lines = []
    def capture_log(msg):
        log_lines.append(msg)
        print(f"  {msg}")

    try:
        t0 = time.time()
        result = run_deep_research(
            query=QUERY,
            server_url=SERVER,
            log=capture_log,
            **params,
        )
        elapsed = round(time.time() - t0, 1)

        report = result.get("report", "")
        stats = result.get("stats", {})

        # Count images in report
        img_count = report.count("![")
        source_count = report.count("### [")

        # Check for image URL encoding issues
        import re
        bad_urls = re.findall(r'\]\((https?://[^)]*[^%20\s][ ])', report)

        summary = {
            "name": name,
            "elapsed": elapsed,
            "stats": stats,
            "report_images": img_count,
            "report_sources": source_count,
            "report_len": len(report),
            "bad_urls": len(bad_urls),
            "images_in_evidence": sum(1 for line in log_lines if "IMG:" in line),
            "alive_count": next((int(line.split("Alive: ")[1].split("/")[0]) for line in log_lines if "Alive:" in line and "/" in line), 0),
            "deep_read_pages": next((int(line.split(" pages read,")[0].split()[-1]) for line in log_lines if "pages read," in line), 0),
            "deep_read_imgs": next((int(line.split(" images extracted")[0].split()[-1]) for line in log_lines if "images extracted" in line), 0),
            "validation_imgs": next((int(line.split("from ")[1].split(" ")[0]) for line in log_lines if "Validation images:" in line), 0),
            "evidence_pages": next((int(line.split("Evidence: ")[1].split(" ")[0]) for line in log_lines if "Evidence:" in line and "pages" in line), 0),
            "query_type": next((line.split("query_type: ")[1].split(" ")[0] for line in log_lines if "query_type:" in line), "unknown"),
        }
        results.append(summary)
        print(f"\n  RESULT: imgs={img_count} sources={source_count} alive={summary['alive_count']} "
              f"deep_read={summary['deep_read_pages']} val_imgs={summary['validation_imgs']} "
              f"bad_urls={summary['bad_urls']} time={elapsed}s")

    except Exception as e:
        print(f"  ERROR: {e}")
        results.append({"name": name, "error": str(e)})

# Summary
print(f"\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
for r in results:
    if "error" in r:
        print(f"  {r['name']}: ERROR - {r['error']}")
    else:
        print(f"  {r['name']}: imgs={r['report_images']} src={r['report_sources']} "
              f"alive={r['alive_count']} deep={r['deep_read_pages']} "
              f"val_i={r['validation_imgs']} bad={r['bad_urls']} "
              f"qt={r['query_type']} {r['elapsed']}s")
