"""备份归档的边界检查，防止路径穿越和覆盖在线数据目录。"""

import io
from pathlib import Path
from tempfile import TemporaryDirectory
import tarfile
import unittest

from deploy.data_backup import _validate_archive


class BackupArchiveTest(unittest.TestCase):
    def test_rejects_path_traversal_and_symlink(self):
        with TemporaryDirectory() as root:
            archive = Path(root) / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                info = tarfile.TarInfo("data/../../.env")
                info.size = 1
                output.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                _validate_archive(archive)

            symlink = Path(root) / "link.tar.gz"
            with tarfile.open(symlink, "w:gz") as output:
                info = tarfile.TarInfo("data/link")
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
                output.addfile(info)
            with self.assertRaises(ValueError):
                _validate_archive(symlink)
