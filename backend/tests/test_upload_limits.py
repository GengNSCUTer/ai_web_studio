from __future__ import annotations

import asyncio
import io
import unittest
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

from app.api.routes.uploads import _read_upload_limited
from app.api.routes import uploads


class UploadLimitTest(unittest.TestCase):
    def test_reads_file_in_chunks_within_limit(self) -> None:
        async def run_test() -> None:
            upload = UploadFile(filename="notes.md", file=io.BytesIO(b"a" * 17))
            content = await _read_upload_limited(upload, max_bytes=17)
            self.assertEqual(content, b"a" * 17)

        asyncio.run(run_test())

    def test_slow_parser_does_not_starve_async_requests(self) -> None:
        async def run_test(root):
            started = threading.Event()
            finished = threading.Event()

            def slow_parse(_path):
                started.set()
                time.sleep(0.2)
                finished.set()
                return "parsed"

            with patch.object(uploads, "settings", replace(uploads.settings, upload_dir=root)), \
                    patch.object(uploads.FileParserService, "parse_file", side_effect=slow_parse):
                task = asyncio.create_task(uploads.upload_files(
                    [UploadFile(filename="notes.md", file=io.BytesIO(b"notes"))], SimpleNamespace(id="user"),
                ))
                while not started.is_set():
                    await asyncio.sleep(0.005)
                # 用事件关系验证让出执行权，避免机器负载造成毫秒阈值误报。
                await asyncio.sleep(0.01)
                self.assertFalse(finished.is_set())
                result = await task
                self.assertEqual(result[0].parsed_text, "parsed")
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(run_test(root))

    def test_batch_parse_failure_removes_previously_written_files(self) -> None:
        async def run_test(root):
            with patch.object(uploads, "settings", replace(uploads.settings, upload_dir=root)), \
                    patch.object(uploads.FileParserService, "parse_file", side_effect=["valid", None]):
                with self.assertRaises(HTTPException):
                    await uploads.upload_files(
                        [UploadFile(filename=f"{n}.md", file=io.BytesIO(b"notes")) for n in range(2)],
                        SimpleNamespace(id="user"),
                    )
                self.assertEqual(list((Path(root) / "user").iterdir()), [])
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(run_test(root))

    def test_parser_concurrency_is_bounded(self) -> None:
        async def run_test(root):
            active = 0
            peak = 0
            lock = threading.Lock()

            def slow_parse(_path):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.04)
                with lock:
                    active -= 1
                return "parsed"

            with patch.object(uploads, "settings", replace(uploads.settings, upload_dir=root)), \
                    patch.object(uploads.FileParserService, "parse_file", side_effect=slow_parse):
                await asyncio.gather(*[
                    uploads.upload_files([UploadFile(filename="notes.md", file=io.BytesIO(b"notes"))],
                                         SimpleNamespace(id="user")) for _ in range(5)
                ])
            self.assertEqual(peak, 2)
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(run_test(root))

    def test_rejects_file_as_soon_as_limit_is_exceeded(self) -> None:
        async def run_test() -> None:
            upload = UploadFile(filename="notes.md", file=io.BytesIO(b"a" * 18))
            with self.assertRaises(HTTPException) as captured:
                await _read_upload_limited(upload, max_bytes=17)
            self.assertEqual(captured.exception.status_code, 400)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
