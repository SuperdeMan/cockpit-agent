#!/usr/bin/env python3
"""Bounded streaming restore for collector SQLite SQL backups."""

from __future__ import annotations

import argparse
import gzip
import sqlite3
from contextlib import closing
from pathlib import Path


DEFAULT_MAX_STATEMENT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_EXPANDED_BYTES = 16 * 1024 * 1024 * 1024


def restore_gzip_sql(
    source: Path, target: Path, *, max_statement_bytes: int = DEFAULT_MAX_STATEMENT_BYTES,
    max_expanded_bytes: int = DEFAULT_MAX_EXPANDED_BYTES,
) -> None:
    if max_statement_bytes <= 0 or max_expanded_bytes <= 0:
        raise ValueError("restore bounds must be positive")
    if target.exists() or target.is_symlink():
        raise ValueError("restore target already exists")
    statement_parts: list[str] = []
    statement_bytes = 0
    expanded_bytes = 0
    try:
        with gzip.open(source, "rt", encoding="utf-8", newline="") as input_file:
            with closing(sqlite3.connect(target)) as connection:
                for line in input_file:
                    line_bytes = len(line.encode("utf-8"))
                    statement_bytes += line_bytes
                    expanded_bytes += line_bytes
                    if expanded_bytes > max_expanded_bytes:
                        raise ValueError("SQLite backup exceeds expanded byte limit")
                    if statement_bytes > max_statement_bytes:
                        raise ValueError("SQLite statement exceeds byte limit")
                    statement_parts.append(line)
                    candidate = "".join(statement_parts)
                    if sqlite3.complete_statement(candidate):
                        connection.execute(candidate)
                        statement_parts.clear()
                        statement_bytes = 0
                if statement_parts and "".join(statement_parts).strip():
                    raise ValueError("SQLite backup ends with an incomplete statement")
                if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise ValueError("restored Collector integrity check failed")
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a bounded gzip SQLite SQL dump")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--max-statement-bytes", type=int, default=DEFAULT_MAX_STATEMENT_BYTES)
    parser.add_argument("--max-expanded-bytes", type=int, default=DEFAULT_MAX_EXPANDED_BYTES)
    args = parser.parse_args()
    restore_gzip_sql(
        args.source, args.target, max_statement_bytes=args.max_statement_bytes,
        max_expanded_bytes=args.max_expanded_bytes,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
