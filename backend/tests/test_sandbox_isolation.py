"""无 Docker 时仍验证命令安全边界、DNS 固定和产物导入；不冒充真实隔离测试。"""

import io
import os
import tarfile
import json
import unittest
from unittest.mock import patch

from app.services.sandbox_policy import SandboxPolicyError, normalize_domain, public_addresses, validate_requirement, validate_wheel_lock
from sandbox.runner import DockerSandbox, SandboxUnavailable


class SandboxIsolationTest(unittest.TestCase):
    def test_invalid_domains_and_dependencies(self):
        for value in ["127.0.0.1", "*.example.com", "https://example.com", "host.internal", "example.com:443", "a..com"]:
            with self.subTest(domain=value), self.assertRaises(SandboxPolicyError):
                normalize_domain(value)
        for value in ["git+https://github.com/pkg", "requests>=2", "pkg @ https://example.com/x.whl", "--extra-index-url=x", "a==1; python_version>'3'"]:
            with self.subTest(requirement=value), self.assertRaises(SandboxPolicyError):
                validate_requirement(value)

    def test_public_target_rejects_mixed_dns_and_returns_pinned_ip(self):
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 443))]):
            self.assertEqual(public_addresses("example.com"), ["93.184.216.34"])
        with patch("socket.getaddrinfo", return_value=[(None, None, None, None, ("10.0.0.1", 443))]):
            with self.assertRaises(SandboxPolicyError):
                public_addresses("example.com")

    def test_wheel_lock_refuses_sources_urls_and_wrong_hash(self):
        row = {"metadata": {"name": "tabulate", "version": "0.9.0"}, "download_info": {
            "url": "https://files.pythonhosted.org/a.whl", "archive_info": {"hashes": {"sha256": "a" * 64}}}}
        self.assertEqual(validate_wheel_lock({"install": [row]})[0]["requirement"], "tabulate==0.9.0")
        for url in ["https://github.com/a.whl", "http://files.pythonhosted.org/a.whl", "https://files.pythonhosted.org/a.tar.gz"]:
            invalid = {**row, "download_info": {**row["download_info"], "url": url}}
            with self.assertRaises(SandboxPolicyError):
                validate_wheel_lock({"install": [invalid]})

    def test_worker_same_uid_cannot_open_docker_even_if_socket_exists(self):
        with patch.dict(os.environ, {"AIWS_WEB_UID": str(os.getuid())}):
            runtime = DockerSandbox()
            with patch.object(runtime, "docker") as docker, self.assertRaisesRegex(SandboxUnavailable, "不同"):
                runtime.preflight()
            docker.assert_not_called()

    def test_container_command_never_mounts_socket_host_or_working_directory(self):
        args = DockerSandbox().create_args("test")
        joined = " ".join(args)
        for flag in ["--read-only", "--user 65532:65532", "--cap-drop=ALL", "--pids-limit=64", "--memory=256m", "--cpus=1", "--network none", "no-new-privileges"]:
            self.assertIn(flag, joined)
        for forbidden in ["--privileged", "--network host", "seccomp=unconfined", "docker.sock", "--volume", "--mount"]:
            self.assertNotIn(forbidden, joined)
        self.assertNotIn("AUTH_SECRET_KEY", DockerSandbox().env)

    def test_input_tar_has_only_approved_copies_and_read_only_permissions(self):
        raw = DockerSandbox.input_tar([{"id": "f", "text": "copy"}], b"print('hi')")
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            self.assertEqual(archive.getnames(), ["main.py", "input/manifest.json", "input/f.txt"])
            self.assertTrue(all(x.mode == 0o444 for x in archive))
        with self.assertRaises(SandboxPolicyError):
            DockerSandbox.input_tar([{"id": "../../config", "text": "bad"}], b"")

    def archive(self, name, *, symlink=False):
        target = io.BytesIO()
        with tarfile.open(fileobj=target, mode="w") as archive:
            item = tarfile.TarInfo(name)
            if symlink:
                item.type, item.linkname = tarfile.SYMTYPE, "/etc/passwd"
                archive.addfile(item)
            else:
                item.size = 4
                archive.addfile(item, io.BytesIO(b"data"))
        return target.getvalue()

    def test_artifact_quarantine_rejects_links_traversal_programs_and_subdirs(self):
        self.assertEqual(DockerSandbox.inspect_artifacts(self.archive("out/result.md"))[0]["content"], "data")
        for name in ["../secret.md", "/out/a.md", "out/program.py", "out/nested/result.md"]:
            with self.subTest(name=name), self.assertRaises(SandboxPolicyError):
                DockerSandbox.inspect_artifacts(self.archive(name))
        with self.assertRaises(SandboxPolicyError):
            DockerSandbox.inspect_artifacts(self.archive("out/link.md", symlink=True))

    def test_installed_dependencies_reject_symlinks_and_escape(self):
        for name in ["out/site/../../secret", "/out/site/a.py", "other/file"]:
            with self.assertRaises(SandboxPolicyError):
                DockerSandbox.validate_dependency_tar(self.archive(name))
        with self.assertRaises(SandboxPolicyError):
            DockerSandbox.validate_dependency_tar(self.archive("out/site/link", symlink=True))
        raw = DockerSandbox.validate_dependency_tar(self.archive("out/site/module.py"))
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            self.assertEqual(archive.getnames(), ["opt/deps/module.py"])
            self.assertEqual(archive.getmembers()[0].mode, 0o444)

    def test_web_does_not_import_docker_runner(self):
        import inspect
        from app.api.routes import plugins
        self.assertNotIn("sandbox.runner", inspect.getsource(plugins))

    def test_dependency_resolution_and_installation_containers_never_receive_task_files(self):
        runtime = DockerSandbox()
        report = {"install": [{"metadata": {"name": "tabulate", "version": "0.9.0"}, "download_info": {
            "url": "https://files.pythonhosted.org/tabulate-0.9.0-py3-none-any.whl",
            "archive_info": {"hashes": {"sha256": "a" * 64}}}}]}
        plan = {"dependencies": ["tabulate==0.9.0"], "files": [{"text": "private note"}], "domains": ["example.com"]}
        with patch.object(runtime, "_run", return_value={"artifacts": [{"name": "lock.json", "content": json.dumps(report)}]}) as execute:
            lock = runtime.resolve_dependencies(plan)
            self.assertEqual(execute.call_args.args[0]["files"], [])
            self.assertEqual(execute.call_args.args[0]["domains"], ["pypi.org", "files.pythonhosted.org"])
            compile(execute.call_args.args[1], "resolver", "exec")
        with patch.object(runtime, "_run", return_value=b"installed-tar") as execute:
            runtime.install_dependencies({**plan, "wheel_lock": lock})
            self.assertEqual(execute.call_args.args[0]["files"], [])
            self.assertEqual(execute.call_args.args[0]["domains"], ["files.pythonhosted.org"])
            compile(execute.call_args.args[1], "installer", "exec")
            self.assertIn(b"--require-hashes", execute.call_args.args[1])

    def test_egress_proxy_rejects_unapproved_domain_and_private_dns_over_real_socket(self):
        import socket
        import threading
        from sandbox.egress_proxy import Proxy, Tunnel
        with Proxy(("127.0.0.1", 0), Tunnel) as server:
            server.allowed_domains = {"example.com"}
            server.denied_ips = set()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for request in [b"CONNECT other.com:443 HTTP/1.1\r\n\r\n", b"CONNECT example.com:443 HTTP/1.1\r\n\r\n",
                                b"CONNECT example.com:22 HTTP/1.1\r\n\r\n"]:
                    with patch("sandbox.egress_proxy.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 443))]):
                        connection = socket.socket()
                        connection.settimeout(3)
                        connection.connect(server.server_address)
                        connection.sendall(request)
                        self.assertIn(b"403 Forbidden", connection.recv(1024))
                        connection.close()
            finally:
                server.shutdown()
                thread.join(timeout=3)
