import os
import sys

import pytest

from remote_code_bridge import BridgeError
from remote_code_bridge.sshconfig import discover_aliases, identity, parse_line, split_words

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission checks")


def ssh_file(home, name, text):
    path = home / ".ssh" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_concrete_aliases_in_order(home):
    config = ssh_file(home, "config", "Host devbox lab\n  HostName 10.0.0.1\nHost *.corp !bad gw? a[bc] -x\nHost=eq\n")
    assert discover_aliases(config, home) == ["devbox", "lab", "eq"]


def test_proxy_command_and_friends_are_allowed(home):
    """B12: v1 refused any config with ProxyCommand; only `Match exec` is dangerous for `ssh -G`."""
    config = ssh_file(home, "config", "Host jump\n  ProxyCommand ssh -W %h:%p bastion\n  LocalCommand echo hi\n")
    assert discover_aliases(config, home) == ["jump"]


def test_match_exec_is_refused(home):
    config = ssh_file(home, "config", 'Match host x exec "true"\nHost a\n')
    with pytest.raises(BridgeError, match="Match exec"):
        discover_aliases(config, home)


def test_relative_includes_resolve_against_dot_ssh(home):
    """B11: OpenSSH resolves `Include other` from ~/.ssh even inside ~/.ssh/config.d/*."""
    ssh_file(home, "config", "Include config.d/*\n")
    ssh_file(home, "config.d/work", "Host work\nInclude shared\n")
    ssh_file(home, "shared", "Host shared-box\n")
    assert discover_aliases(home / ".ssh" / "config", home) == ["work", "shared-box"]


def test_tilde_and_absolute_includes(home, tmp_path):
    absolute = tmp_path / "abs.conf"
    absolute.write_text("Host abs\n")
    absolute.chmod(0o600)
    ssh_file(home, "config", f'Include ~/.ssh/t.conf "{absolute}"\n')
    ssh_file(home, "t.conf", "Host tilde\n")
    assert discover_aliases(home / ".ssh" / "config", home) == ["tilde", "abs"]


def test_include_cycles_and_missing_files(home):
    ssh_file(home, "config", "Include config\nInclude nope\nHost a\n")
    assert discover_aliases(home / ".ssh" / "config", home) == ["a"]
    assert discover_aliases(home / ".ssh" / "missing", home) == []


def test_include_depth_limit(home):
    for index in range(20):
        ssh_file(home, f"c{index}", f"Include c{index + 1}\n")
    ssh_file(home, "c20", "Host deep\n")
    with pytest.raises(BridgeError, match="recursion"):
        discover_aliases(home / ".ssh" / "c0", home)


def test_empty_include_and_bad_quotes(home):
    with pytest.raises(BridgeError, match="empty SSH Include"):
        discover_aliases(ssh_file(home, "config", "Include\n"), home)
    with pytest.raises(BridgeError, match="unbalanced"):
        discover_aliases(ssh_file(home, "config", 'Host "a\n'), home)


@posix_only
def test_unsafe_permissions_are_refused(home):
    config = ssh_file(home, "config", "Host a\n")
    config.chmod(0o620)
    with pytest.raises(BridgeError, match="writable"):
        discover_aliases(config, home)


@posix_only
def test_symlinked_config_is_followed(home, tmp_path):
    real = tmp_path / "dotfiles-ssh-config"
    real.write_text("Host dot\n")
    real.chmod(0o600)
    (home / ".ssh").mkdir()
    os.symlink(real, home / ".ssh" / "config")
    assert discover_aliases(home / ".ssh" / "config", home) == ["dot"]


def test_parsing_helpers():
    assert parse_line("  # comment", "x") is None
    assert parse_line("HostName=example.com", "x") == ("hostname", ["example.com"])
    assert parse_line('Include "a b" c # tail', "x") == ("include", ["a b", "c"])
    assert split_words('a "" b', "x") == ["a", "", "b"]


def test_identity_uses_ssh_g(fake_ssh, monkeypatch):
    monkeypatch.setenv("FAKE_SSH_HOSTS", '{"devbox": "10.0.0.5 me 2222"}')
    monkeypatch.setenv("FAKE_SSH_G_FAIL", "broken")
    assert identity("devbox") == ("10.0.0.5", "me", "2222")
    assert identity("broken") is None
