"""Worker entry point.

Two modes:

* CLI (default): ``python -m scanner.main <url>`` runs a single scan and prints
  the report. ``--json`` prints machine-readable JSON instead.

* Queue worker: ``python -m scanner.main --worker`` blocks on a Redis list
  (``klarim:scan_queue``), pops target URLs, scans them, and stores the JSON
  report back in Redis (``klarim:report:<url>``). This is the shape consumed by
  the ``worker`` service in ``docker-compose.yml``. Redis is optional — if it is
  unavailable, the worker mode explains how to run a one-off scan instead.

The worker deliberately stays thin: it is glue around ``scanner.run_scan``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from .runner import run_scan, format_report


SCAN_QUEUE = os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue")
REPORT_PREFIX = os.environ.get("KLARIM_REPORT_PREFIX", "klarim:report:")


async def _scan_and_print(url: str, as_json: bool, as_pdf: bool) -> int:
    report = await run_scan(url)
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_report(report))

    if as_pdf:
        await _write_pdfs(report, url)

    # Exit non-zero when the target is in the red, so CI/cron can react.
    return 0 if (report.score and report.score.score >= 50) else 1


async def _write_pdfs(report, url: str) -> None:
    """Generate the executive + technical PDFs into the current directory."""
    # Imported lazily: pulls in weasyprint/jinja2 only when --pdf is used.
    from reporter import (
        generate_executive_pdf,
        generate_technical_pdf,
        pdf_filename,
    )

    exec_bytes = await generate_executive_pdf(report, url)
    tech_bytes = await generate_technical_pdf(report, url)
    exec_name = pdf_filename("executive", url, report.started_at)
    tech_name = pdf_filename("technical", url, report.started_at)
    with open(exec_name, "wb") as fh:
        fh.write(exec_bytes)
    with open(tech_name, "wb") as fh:
        fh.write(tech_bytes)
    print(f"\nPDFs gerados:\n  - {exec_name} ({len(exec_bytes)} bytes)\n  - {tech_name} ({len(tech_bytes)} bytes)")


def _run_worker() -> int:
    try:
        import redis  # imported lazily; only needed in worker mode
    except ImportError:
        print(
            "redis package not installed. Install requirements.txt or run a "
            "one-off scan: python -m scanner.main <url>",
            file=sys.stderr,
        )
        return 2

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.from_url(redis_url, decode_responses=True)
        client.ping()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not connect to Redis at {redis_url}: {exc!r}", file=sys.stderr)
        return 2

    print(f"[klarim-worker] connected to {redis_url}; waiting on '{SCAN_QUEUE}'…")
    while True:
        item = client.blpop(SCAN_QUEUE, timeout=0)
        if not item:
            continue
        _, url = item
        print(f"[klarim-worker] scanning {url}")
        try:
            report = asyncio.run(run_scan(url))
            client.set(
                REPORT_PREFIX + url,
                json.dumps(report.to_dict(), ensure_ascii=False),
            )
            score = report.score.score if report.score else "n/a"
            print(f"[klarim-worker] done {url} -> score {score}")
        except Exception as exc:  # noqa: BLE001 - keep the worker alive
            print(f"[klarim-worker] error scanning {url}: {exc!r}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="klarim-scanner",
        description="Klarim passive web-security scanner (passive checks, in continuous expansion).",
    )
    parser.add_argument("url", nargs="?", help="Target URL to scan.")
    parser.add_argument(
        "--worker",
        action="store_true",
        help="Run as a Redis queue worker instead of a one-off scan.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the report as JSON (single-scan mode).",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="Also write executive + technical PDF reports to the current directory.",
    )
    args = parser.parse_args(argv)

    if args.worker:
        return _run_worker()

    if not args.url:
        parser.error("provide a URL to scan, or use --worker")

    return asyncio.run(_scan_and_print(args.url, args.json, args.pdf))


if __name__ == "__main__":
    raise SystemExit(main())
