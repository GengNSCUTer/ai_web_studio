"""在短暂停写期间备份，恢复只允许空的隔离目标；不复制凭据配置。"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.config import settings

WRITERS = ("backend", "knowledge-worker", "memory-worker", "durable-worker")


def pg_env(url):
    # 密码仅通过子进程环境传递，不拼在命令行或日志里。
    return {**os.environ, "PGHOST": url.host or "localhost", "PGPORT": str(url.port or 5432),
            "PGUSER": url.username or "", "PGPASSWORD": url.password or "", "PGDATABASE": url.database or ""}


def run_pg(binary, arguments, url):
    result = subprocess.run([binary, *arguments], env=pg_env(url), capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{Path(binary).name} 失败；未输出可能包含连接信息的原始错误")


@contextmanager
def quiesce():
    """只暂停原本运行中的本项目写入者，任何退出路径均尝试恢复。"""
    def ctl(*args):
        return subprocess.run(["bash", str(ROOT / "deploy/run_services.sh"), "ctl", *args],
                              cwd=ROOT, capture_output=True, text=True)
    status = ctl("status")
    if not status.stdout or "backend" not in status.stdout:
        raise RuntimeError("无法确认本项目托管服务状态，拒绝不一致的在线备份")
    running = [name for name in WRITERS if any(line.split()[:2] == [name, "RUNNING"]
               for line in status.stdout.splitlines())]
    try:
        for name in running:
            if ctl("stop", name).returncode:
                raise RuntimeError(f"无法暂停 {name}，备份取消")
        yield
    finally:
        failed = [name for name in running if ctl("start", name).returncode]
        if failed:
            raise RuntimeError("备份结束后部分服务未启动：" + ", ".join(failed))


def _archive(source, destination):
    source = Path(source).resolve()
    # 备份不跟随链接，防止将 uploads 外的配置或私钥打包。
    if source.is_symlink() or (source.exists() and any(path.is_symlink() for path in source.rglob("*"))):
        raise ValueError("数据目录中存在符号链接，需人工确认后再备份")
    with tarfile.open(destination, "w:gz") as archive:
        if source.exists():
            archive.add(source, arcname="data", recursive=True)


def backup(destination, *, source_url, upload_root, index_root, pg_bin):
    destination = Path(destination).resolve()
    for root in (upload_root, index_root):
        if destination == Path(root).resolve() or Path(root).resolve() in destination.parents:
            raise ValueError("备份目录不能位于待备份的数据目录内")
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(destination, 0o700)
    try:
        run_pg(str(Path(pg_bin) / "pg_dump"), ["--format=custom", "--no-owner", "--no-acl",
               "--file", str(destination / "database.dump")], source_url)
        _archive(upload_root, destination / "uploads.tar.gz")
        _archive(index_root, destination / "indexes.tar.gz")
        manifest = {"format_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                    "database": source_url.database, "complete": True}
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for path in destination.iterdir():
            os.chmod(path, 0o600)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination


def _validate_archive(path):
    with tarfile.open(path, "r:gz") as archive:
        for member in archive.getmembers():
            parts = Path(member.name).parts
            if not parts or parts[0] != "data" or ".." in parts or Path(member.name).is_absolute():
                raise ValueError("备份归档包含越界路径")
            if not (member.isfile() or member.isdir()):
                raise ValueError("备份归档包含链接或特殊文件")


def restore(source, *, target_url, upload_root, index_root, pg_bin):
    source = Path(source).resolve()
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1 or not manifest.get("complete"):
        raise ValueError("备份未完成或格式不支持")
    # 明确禁止直接覆盖运行库；目标库由运维预先创建为空库。
    if target_url.database in {settings.postgres_db, manifest["database"], "postgres", "template0", "template1"}:
        raise ValueError("恢复必须使用不同于源库和当前运行库的隔离数据库")
    paths = [Path(upload_root).resolve(), Path(index_root).resolve()]
    protected = [Path(settings.upload_dir).resolve(), Path(settings.knowledge_index_dir).resolve(), source]
    for target in paths:
        if target.exists() or any(target == p or p in target.parents or target in p.parents for p in protected):
            raise ValueError("恢复文件目录必须是未存在的隔离路径")
    if any(a == b or a in b.parents or b in a.parents for a, b in [(paths[0], paths[1])]):
        raise ValueError("两个恢复目录不能重叠")
    _validate_archive(source / "uploads.tar.gz")
    _validate_archive(source / "indexes.tar.gz")
    target_engine = create_engine(target_url)
    try:
        with target_engine.connect() as connection:
            if inspect(connection).get_table_names(schema="public"):
                raise ValueError("目标数据库非空，拒绝覆盖")
        # 先解压到最终隔离目录，失败时删除本次目录；数据库恢复为单事务。
        for target, archive_name in zip(paths, ("uploads.tar.gz", "indexes.tar.gz")):
            target.mkdir(mode=0o700, parents=True)
            with tarfile.open(source / archive_name, "r:gz") as archive:
                for member in archive.getmembers():
                    relative = Path(member.name).relative_to("data")
                    output = target / relative
                    if member.isdir():
                        output.mkdir(mode=0o700, parents=True, exist_ok=True)
                    else:
                        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        with archive.extractfile(member) as src, output.open("xb") as dst:
                            shutil.copyfileobj(src, dst)
                        os.chmod(output, 0o600)
        run_pg(str(Path(pg_bin) / "pg_restore"), ["--single-transaction", "--exit-on-error",
               "--no-owner", "--no-acl", "--dbname", target_url.database, str(source / "database.dump")], target_url)
    except BaseException:
        for path in paths:
            if path.exists():
                shutil.rmtree(path)
        raise
    finally:
        target_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--pg-bin", default=os.getenv("AIWS_PG_BIN", ""))
    parser.add_argument("--target-db")
    parser.add_argument("--uploads", type=Path)
    parser.add_argument("--indexes", type=Path)
    args = parser.parse_args()
    from sqlalchemy.engine import make_url
    url = make_url(settings.sqlalchemy_database_uri)
    if args.action == "backup":
        with quiesce():
            backup(args.path, source_url=url, upload_root=settings.upload_dir,
                   index_root=settings.knowledge_index_dir, pg_bin=args.pg_bin)
    else:
        if not all([args.target_db, args.uploads, args.indexes]):
            parser.error("恢复必须指定 --target-db、--uploads 和 --indexes")
        restore(args.path, target_url=url.set(database=args.target_db), upload_root=args.uploads,
                index_root=args.indexes, pg_bin=args.pg_bin)
    print(json.dumps({"action": args.action, "status": "ok"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"数据操作失败：{type(exc).__name__}；源数据未被覆盖", file=sys.stderr)
        sys.exit(1)
