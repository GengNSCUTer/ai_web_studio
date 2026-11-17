"""探测不是执行许可：缺环境、超时或特权程序均不自动退回宿主运行。"""

import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('sandbox_probe', Path(__file__).resolve().parents[2] / 'scripts/probe_sandbox_environment.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class SandboxEnvironmentProbeTest(unittest.TestCase):
    def test_timeout_is_recorded_and_never_falls_back(self):
        with patch.object(probe.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixed',10)) as run:
            self.assertEqual(probe.run_fixed(['fixed'])['error'], 'TimeoutExpired')
            self.assertEqual(run.call_count, 1)

    def test_probe_process_does_not_inherit_secrets(self):
        with patch.dict(probe.os.environ, {'SECRET_KEY': 'synthetic'}), patch.object(probe.subprocess,'run',
                return_value=subprocess.CompletedProcess([],0,'ok','')) as run:
            probe.run_fixed(['fixed'])
            self.assertEqual(set(run.call_args.kwargs['env']), {'PATH','LC_ALL'})
            self.assertTrue(run.call_args.kwargs['close_fds'])

    def test_missing_binary_is_not_execution_authorization(self):
        with patch.object(probe.shutil,'which',return_value=None), patch.object(probe,'run_fixed') as run:
            result=probe.inspect_environment()
            self.assertFalse(result['execution_enabled'])
            self.assertEqual(result['bwrap_probe']['error'],'missing_or_privileged_binary')
            run.assert_not_called()
