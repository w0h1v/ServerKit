"""Tests for backend/app/utils/system.py.

Uses a direct import path so the test can run without Flask dependencies
(system.py itself has no Flask imports).
"""

import importlib
import os
import subprocess
import sys
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# Import system.py directly to avoid triggering app/__init__.py (Flask deps).
# We register stub entries for 'app' and 'app.utils' so that
# unittest.mock.patch('app.utils.system.X') can resolve the path without
# importing the real app package (which needs Flask).
import types

_backend = os.path.join(os.path.dirname(__file__), os.pardir)
_mod_path = os.path.join(_backend, 'app', 'utils', 'system.py')
_spec = importlib.util.spec_from_file_location('app.utils.system', _mod_path)
_module = importlib.util.module_from_spec(_spec)

# Stub parent packages so patch() resolution never hits Flask imports.
if 'app' not in sys.modules:
    sys.modules['app'] = types.ModuleType('app')
if 'app.utils' not in sys.modules:
    _utils = types.ModuleType('app.utils')
    sys.modules['app.utils'] = _utils
    sys.modules['app'].utils = _utils  # type: ignore[attr-defined]

sys.modules['app.utils.system'] = _module
sys.modules['app.utils'].system = _module  # type: ignore[attr-defined]
_spec.loader.exec_module(_module)

run_privileged = _module.run_privileged
privileged_cmd = _module.privileged_cmd
is_command_available = _module.is_command_available
PackageManager = _module.PackageManager
ServiceControl = _module.ServiceControl


# ---------------------------------------------------------------------------
# run_privileged
# ---------------------------------------------------------------------------
class TestRunPrivileged:
    """Tests for :func:`run_privileged`."""

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_prepends_sudo_when_not_root(self, _euid, mock_run, _which):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged(['systemctl', 'restart', 'nginx'])
        mock_run.assert_called_once_with(
            ['sudo', '-n', 'systemctl', 'restart', 'nginx'],
            capture_output=True, text=True, timeout=_module.DEFAULT_PRIVILEGED_TIMEOUT,
        )

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=0, create=True)
    def test_skips_sudo_when_root(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged(['systemctl', 'restart', 'nginx'])
        argv = mock_run.call_args.args[0]
        # No sudo when we are already root...
        assert 'sudo' not in argv
        # ...but argv[0] is resolved to an absolute path rather than left bare.
        # A systemd unit's PATH may omit sbin, and execing a bare name through it
        # raised FileNotFoundError for iptables/ufw/nft even though the binaries
        # were installed (the metadata-guard rule then silently never installed).
        assert argv[0].startswith('/') and argv[0].endswith('/systemctl')
        assert argv[1:] == ['restart', 'nginx']
        assert mock_run.call_args.kwargs == {
            'capture_output': True, 'text': True,
            'timeout': _module.DEFAULT_PRIVILEGED_TIMEOUT,
        }

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=0, create=True)
    def test_root_keeps_bare_name_when_unresolvable(self, _euid, mock_run):
        """An unknown command stays bare so the caller sees the usual error."""
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged(['definitely-not-a-real-binary-xyz', '--help'])
        assert mock_run.call_args.args[0][0] == 'definitely-not-a-real-binary-xyz'

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=0, create=True)
    def test_root_leaves_absolute_path_untouched(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged(['/usr/bin/env', 'true'])
        assert mock_run.call_args.args[0] == ['/usr/bin/env', 'true']

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_no_double_sudo(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        # An explicitly-sudo command is passed through untouched (no `sudo sudo`,
        # and no second-guessing a caller that built its own invocation).
        run_privileged(['sudo', 'systemctl', 'restart', 'nginx'])
        mock_run.assert_called_once_with(
            ['sudo', 'systemctl', 'restart', 'nginx'],
            capture_output=True, text=True, timeout=_module.DEFAULT_PRIVILEGED_TIMEOUT,
        )

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_defaults_applied(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged(['ls'])
        _, kwargs = mock_run.call_args
        assert kwargs['capture_output'] is True
        assert kwargs['text'] is True

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_kwargs_passed_through(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged(['apt', 'install', '-y', 'foo'], timeout=120, check=True)
        _, kwargs = mock_run.call_args
        assert kwargs['timeout'] == 120
        assert kwargs['check'] is True

    # Output is captured and no terminal is attached, so a command that never
    # returns would hang its caller silently and forever. Every call gets a
    # ceiling unless one is named explicitly.
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_default_timeout_applied(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged(['systemctl', 'restart', 'nginx'])
        _, kwargs = mock_run.call_args
        assert kwargs['timeout'] == _module.DEFAULT_PRIVILEGED_TIMEOUT

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_timeout_can_be_disabled_explicitly(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged(['tail', '-f', '/var/log/syslog'], timeout=None)
        _, kwargs = mock_run.call_args
        assert kwargs['timeout'] is None

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_caller_can_override_defaults(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged(['ls'], capture_output=False)
        _, kwargs = mock_run.call_args
        assert kwargs['capture_output'] is False

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_string_command_gets_sudo(self, _euid, mock_run, _which):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged('systemctl restart nginx')
        args, _ = mock_run.call_args
        assert args[0] == 'sudo -n systemctl restart nginx'

    # Regression: bare `sudo` blocks forever on a password prompt when the panel
    # runs non-root with output captured — nothing can type the password and
    # nothing can see it. This hung backend startup (metadata guard iptables
    # probe). Every generated sudo invocation must be non-interactive.
    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_sudo_is_always_non_interactive(self, _euid, _which):
        as_list = privileged_cmd(['iptables', '-C', 'DOCKER-USER'])
        assert as_list[:2] == ['sudo', '-n']

        as_user = privileged_cmd(['whoami'], user='deploy')
        assert as_user[:4] == ['sudo', '-n', '-u', 'deploy']

        as_string = privileged_cmd('iptables -C DOCKER-USER')
        assert as_string.startswith('sudo -n ')

        as_user_string = privileged_cmd('whoami', user='deploy')
        assert as_user_string.startswith('sudo -n -u deploy ')

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_string_command_no_double_sudo(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        run_privileged('sudo systemctl restart nginx')
        args, _ = mock_run.call_args
        assert args[0] == 'sudo systemctl restart nginx'

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_returns_completed_process(self, _euid, mock_run):
        expected = subprocess.CompletedProcess([], 0, stdout='ok')
        mock_run.return_value = expected
        result = run_privileged(['echo', 'ok'])
        assert result is expected


# ---------------------------------------------------------------------------
# is_command_available
# ---------------------------------------------------------------------------
class TestIsCommandAvailable:
    """Tests for :func:`is_command_available`."""

    @patch('app.utils.system.shutil.which', return_value='/usr/bin/nginx')
    def test_found_in_path(self, _which):
        assert is_command_available('nginx') is True

    @patch('app.utils.system.os.path.exists', return_value=True)
    @patch('app.utils.system.shutil.which', return_value=None)
    def test_found_in_common_paths(self, _which, _exists):
        assert is_command_available('firewall-cmd') is True

    @patch('app.utils.system.os.path.exists', return_value=False)
    @patch('app.utils.system.shutil.which', return_value=None)
    def test_not_found(self, _which, _exists):
        assert is_command_available('nonexistent') is False


# ---------------------------------------------------------------------------
# PackageManager
# ---------------------------------------------------------------------------
class TestPackageManager:
    """Tests for :class:`PackageManager`."""

    def setup_method(self):
        PackageManager.reset_cache()

    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/apt' if c == 'apt' else None)
    def test_detect_apt(self, _which):
        assert PackageManager.detect() == 'apt'

    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/dnf' if c == 'dnf' else None)
    def test_detect_dnf(self, _which):
        assert PackageManager.detect() == 'dnf'

    @patch('app.utils.system.shutil.which', return_value=None)
    def test_detect_none(self, _which):
        assert PackageManager.detect() is None

    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/apt' if c == 'apt' else None)
    def test_is_available_true(self, _which):
        assert PackageManager.is_available() is True

    @patch('app.utils.system.shutil.which', return_value=None)
    def test_is_available_false(self, _which):
        assert PackageManager.is_available() is False

    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/apt' if c == 'apt' else None)
    def test_detect_caches(self, mock_which):
        PackageManager.detect()
        PackageManager.detect()
        # shutil.which should only be called during the first detect()
        assert mock_which.call_count <= 3  # at most apt/dnf/yum on first call

    # -- is_installed --

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/apt' if c == 'apt' else None)
    def test_is_installed_apt_true(self, _which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            [], 0, stdout='Status: install ok installed\n',
        )
        assert PackageManager.is_installed('nginx') is True
        mock_run.assert_called_once_with(
            ['dpkg', '-s', 'nginx'], capture_output=True, text=True, timeout=_module.PROBE_TIMEOUT,
        )

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/apt' if c == 'apt' else None)
    def test_is_installed_apt_false(self, _which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 1, stdout='')
        assert PackageManager.is_installed('nginx') is False

    @patch('app.utils.system.subprocess.run', side_effect=FileNotFoundError)
    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/apt' if c == 'apt' else None)
    def test_is_installed_apt_dpkg_missing(self, _which, _run):
        assert PackageManager.is_installed('nginx') is False

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/dnf' if c == 'dnf' else None)
    def test_is_installed_rpm_true(self, _which, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='nginx-1.0\n')
        assert PackageManager.is_installed('nginx') is True

    @patch('app.utils.system.subprocess.run', side_effect=FileNotFoundError)
    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/dnf' if c == 'dnf' else None)
    def test_is_installed_rpm_missing(self, _which, _run):
        assert PackageManager.is_installed('nginx') is False

    @patch('app.utils.system.shutil.which', return_value=None)
    def test_is_installed_no_manager(self, _which):
        assert PackageManager.is_installed('nginx') is False

    # -- install --

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/apt' if c == 'apt' else ('/usr/bin/sudo' if c == 'sudo' else None))
    def test_install_apt(self, _which, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        result = PackageManager.install(['nginx', 'curl'])
        mock_run.assert_called_once_with(
            ['sudo', '-n', 'apt', 'install', '-y', 'nginx', 'curl'],
            capture_output=True, text=True, timeout=300,
        )

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    @patch('app.utils.system.shutil.which', side_effect=lambda c: '/usr/bin/dnf' if c == 'dnf' else ('/usr/bin/sudo' if c == 'sudo' else None))
    def test_install_dnf(self, _which, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        PackageManager.install('nginx')
        args = mock_run.call_args[0][0]
        assert args == ['sudo', '-n', 'dnf', 'install', '-y', 'nginx']

    @patch('app.utils.system.shutil.which', return_value=None)
    def test_install_no_manager_raises(self, _which):
        with pytest.raises(RuntimeError, match='No supported package manager'):
            PackageManager.install('nginx')


# ---------------------------------------------------------------------------
# ServiceControl
# ---------------------------------------------------------------------------
class TestServiceControl:
    """Tests for :class:`ServiceControl`."""

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_start(self, _euid, mock_run, _which):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        ServiceControl.start('nginx')
        mock_run.assert_called_once_with(
            ['sudo', '-n', 'systemctl', 'start', 'nginx'],
            capture_output=True, text=True, timeout=_module.DEFAULT_PRIVILEGED_TIMEOUT,
        )

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_stop(self, _euid, mock_run, _which):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        ServiceControl.stop('nginx')
        mock_run.assert_called_once_with(
            ['sudo', '-n', 'systemctl', 'stop', 'nginx'],
            capture_output=True, text=True, timeout=_module.DEFAULT_PRIVILEGED_TIMEOUT,
        )

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_restart(self, _euid, mock_run, _which):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        ServiceControl.restart('nginx')
        mock_run.assert_called_once_with(
            ['sudo', '-n', 'systemctl', 'restart', 'nginx'],
            capture_output=True, text=True, timeout=_module.DEFAULT_PRIVILEGED_TIMEOUT,
        )

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_reload(self, _euid, mock_run, _which):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        ServiceControl.reload('nginx')
        mock_run.assert_called_once_with(
            ['sudo', '-n', 'systemctl', 'reload', 'nginx'],
            capture_output=True, text=True, timeout=_module.DEFAULT_PRIVILEGED_TIMEOUT,
        )

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_enable(self, _euid, mock_run, _which):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        ServiceControl.enable('nginx')
        mock_run.assert_called_once_with(
            ['sudo', '-n', 'systemctl', 'enable', 'nginx'],
            capture_output=True, text=True, timeout=_module.DEFAULT_PRIVILEGED_TIMEOUT,
        )

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_disable(self, _euid, mock_run, _which):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        ServiceControl.disable('nginx')
        mock_run.assert_called_once_with(
            ['sudo', '-n', 'systemctl', 'disable', 'nginx'],
            capture_output=True, text=True, timeout=_module.DEFAULT_PRIVILEGED_TIMEOUT,
        )

    @patch('app.utils.system.os.name', 'posix')
    @patch('app.utils.system.shutil.which', return_value='/usr/bin/sudo')
    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_daemon_reload(self, _euid, mock_run, _which):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        ServiceControl.daemon_reload()
        mock_run.assert_called_once_with(
            ['sudo', '-n', 'systemctl', 'daemon-reload'],
            capture_output=True, text=True, timeout=_module.DEFAULT_PRIVILEGED_TIMEOUT,
        )

    @patch('app.utils.system.subprocess.run')
    @patch('app.utils.system.os.geteuid', return_value=1000, create=True)
    def test_restart_with_kwargs(self, _euid, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0)
        ServiceControl.restart('nginx', check=True, timeout=30)
        _, kwargs = mock_run.call_args
        assert kwargs['check'] is True
        assert kwargs['timeout'] == 30

    @patch('app.utils.system.subprocess.run')
    def test_is_active_true(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='active\n')
        assert ServiceControl.is_active('nginx') is True

    @patch('app.utils.system.subprocess.run')
    def test_is_active_false(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 3, stdout='inactive\n')
        assert ServiceControl.is_active('nginx') is False

    @patch('app.utils.system.subprocess.run', side_effect=FileNotFoundError)
    def test_is_active_missing_systemctl(self, _run):
        assert ServiceControl.is_active('nginx') is False

    @patch('app.utils.system.subprocess.run')
    def test_is_enabled_true(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout='enabled\n')
        assert ServiceControl.is_enabled('nginx') is True

    @patch('app.utils.system.subprocess.run')
    def test_is_enabled_false(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess([], 1, stdout='disabled\n')
        assert ServiceControl.is_enabled('nginx') is False

    @patch('app.utils.system.subprocess.run', side_effect=FileNotFoundError)
    def test_is_enabled_missing_systemctl(self, _run):
        assert ServiceControl.is_enabled('nginx') is False
