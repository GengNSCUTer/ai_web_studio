from __future__ import annotations

import asyncio
import io
import unittest

from fastapi import HTTPException, UploadFile

from app.api.routes.uploads import _read_upload_limited


class UploadLimitTest(unittest.TestCase):
    def test_reads_file_in_chunks_within_limit(self) -> None:
        async def run_test() -> None:
            upload = UploadFile(filename="notes.md", file=io.BytesIO(b"a" * 17))
            content = await _read_upload_limited(upload, max_bytes=17)
            self.assertEqual(content, b"a" * 17)

        asyncio.run(run_test())

    def test_rejects_file_as_soon_as_limit_is_exceeded(self) -> None:
        async def run_test() -> None:
            upload = UploadFile(filename="notes.md", file=io.BytesIO(b"a" * 18))
            with self.assertRaises(HTTPException) as captured:
                await _read_upload_limited(upload, max_bytes=17)
            self.assertEqual(captured.exception.status_code, 400)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
