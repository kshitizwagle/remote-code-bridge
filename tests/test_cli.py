import threading

import pytest
from conftest import TOKEN

from remote_code_bridge import __version__
from remote_code_bridge.cli import main
from remote_code_bridge.config import HostConfig
from remote_code_bridge.server import BridgeServer


@pytest.fixture
def running(home):
    config = HostConfig(port=0, token=TOKEN, default_host="devbox", dry_run=True, tunnel_enabled=False)
    bridge = BridgeServer(config)
    threading.Thread(target=bridge.serve_forever, daemon=True).start()
    yield bridge.server_address[1]
    bridge.shutdown()
    bridge.server_close()


def write_config(home, name, text):
    directory = home / ".config" / "remote-code-bridge"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text)


def test_invoked_as_code_opens(home, running, capsys):
    write_config(home, "remote.env", f"REMOTE_CODE_BRIDGE_PORT={running}\nREMOTE_CODE_BRIDGE_HOST_ALIAS=devbox\n"
                                     f"REMOTE_CODE_BRIDGE_TOKEN={TOKEN}\n")  # fmt: skip
    assert main(["/home/u/.local/bin/code", "--reuse-window", "/srv/app"]) == 0
    assert capsys.readouterr().out.strip() == "dry-run command: code --reuse-window --remote ssh-remote+devbox /srv/app"


def test_errors_exit_2_with_prefix(home, capsys):
    assert main(["remote-code-bridge", "open", "/x"]) == 2
    assert capsys.readouterr().err.startswith("remote-code-bridge: REMOTE_CODE_BRIDGE_HOST_ALIAS")
    assert main(["remote-code-bridge", "serve"]) == 2
    assert "REMOTE_CODE_BRIDGE_TOKEN is required" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv,message",
    [
        (["frobnicate"], "usage:"),
        (["generate-token", "extra"], "does not accept arguments"),
        (["install", "a", "b"], "usage: remote-code-bridge install"),
        (["install", "--force"], "usage: remote-code-bridge install"),
        (["install", "--service", "--no-service"], "either --service or --no-service"),
        (["uninstall", "--everything"], "usage: remote-code-bridge uninstall"),
        (["update", "a", "b"], "usage: remote-code-bridge update"),
    ],
)
def test_usage_errors(home, capsys, argv, message):
    assert main(["remote-code-bridge", *argv]) == 2
    assert message in capsys.readouterr().err


def test_simple_commands(capsys):
    assert main(["remote-code-bridge", "generate-token"]) == 0
    assert len(capsys.readouterr().out.strip()) == 64
    assert main(["remote-code-bridge", "--version"]) == 0
    assert __version__ in capsys.readouterr().out
    assert main(["remote-code-bridge"]) == 0
    assert "usage" in capsys.readouterr().out


def test_status_not_installed(home, capsys):
    assert main(["remote-code-bridge", "status"]) == 2
    assert "not installed" in capsys.readouterr().err


def test_status_host_and_remote(home, running, capsys):
    write_config(home, "host.env", f"REMOTE_CODE_BRIDGE_PORT={running}\nREMOTE_CODE_BRIDGE_TOKEN={TOKEN}\n")
    write_config(home, "remote.env", f"REMOTE_CODE_BRIDGE_PORT={running}\nREMOTE_CODE_BRIDGE_HOST_ALIAS=devbox\n"
                                     f"REMOTE_CODE_BRIDGE_TOKEN={TOKEN}\n")  # fmt: skip
    assert main(["remote-code-bridge", "status"]) == 0
    out = capsys.readouterr().out
    assert "host bridge: running" in out and "tunnel:      disabled" in out and "token:       accepted" in out


def test_status_reports_a_rejected_token(home, running, capsys):
    write_config(home, "remote.env", f"REMOTE_CODE_BRIDGE_PORT={running}\nREMOTE_CODE_BRIDGE_HOST_ALIAS=devbox\n"
                                     f"REMOTE_CODE_BRIDGE_TOKEN={'f' * 64}\n")  # fmt: skip
    assert main(["remote-code-bridge", "status"]) == 1
    assert "rejected (unauthorized)" in capsys.readouterr().out


def test_status_when_nothing_is_running(home, capsys):
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    write_config(home, "host.env", f"REMOTE_CODE_BRIDGE_PORT={port}\n")
    write_config(home, "remote.env", f"REMOTE_CODE_BRIDGE_PORT={port}\n")
    assert main(["remote-code-bridge", "status"]) == 1
    out = capsys.readouterr().out
    assert "host bridge: not running" in out and "tunnel:      not connected" in out
