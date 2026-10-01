import pytest
from conftest import TOKEN

from remote_code_bridge import BridgeError, generate_token, is_valid_alias
from remote_code_bridge.config import (
    HostConfig,
    RemoteConfig,
    read_host_config,
    read_remote_config,
    read_values,
    update_env_text,
)


def no_env(_key):
    return None


def write(tmp_path, text):
    path = tmp_path / "config.env"
    path.write_text(text)
    return path


def test_remote_config_is_read_from_file(tmp_path):
    path = write(
        tmp_path,
        "# comment\nREMOTE_CODE_BRIDGE_PORT=4010\nREMOTE_CODE_BRIDGE_HOST_ALIAS=devbox\n"
        f"REMOTE_CODE_BRIDGE_TOKEN={TOKEN}\n"
        "REMOTE_CODE_BRIDGE_SOCKET=/home/u/.cache/remote-code-bridge/bridge.sock\n",
    )
    assert read_remote_config(path, no_env) == RemoteConfig(
        port=4010, host_alias="devbox", token=TOKEN, socket="/home/u/.cache/remote-code-bridge/bridge.sock"
    )


def test_non_empty_environment_overrides_file(tmp_path):
    path = write(tmp_path, "REMOTE_CODE_BRIDGE_HOST_ALIAS=file\nREMOTE_CODE_BRIDGE_PORT=4010\n")
    environment = {"REMOTE_CODE_BRIDGE_HOST_ALIAS": "env", "REMOTE_CODE_BRIDGE_PORT": ""}
    config = read_remote_config(path, environment.get)
    assert config.host_alias == "env"
    assert config.port == 4010  # an empty environment value does not override


def test_missing_file_gives_defaults(tmp_path):
    assert read_host_config(tmp_path / "missing.env", no_env) == HostConfig()


def test_host_rejects_non_localhost_bind(tmp_path):
    with pytest.raises(BridgeError, match="127.0.0.1"):
        read_host_config(write(tmp_path, "REMOTE_CODE_BRIDGE_BIND=0.0.0.0\n"), no_env)


@pytest.mark.parametrize("port", ["0", "65536", "abc"])
def test_invalid_ports_are_rejected(tmp_path, port):
    with pytest.raises(BridgeError, match="REMOTE_CODE_BRIDGE_PORT"):
        read_host_config(write(tmp_path, f"REMOTE_CODE_BRIDGE_PORT={port}\n"), no_env)


@pytest.mark.parametrize("token", ["not-a-token", "good\rInjected: header", TOKEN[:-1]])
def test_tokens_must_be_64_hex_characters(tmp_path, token):
    path = write(tmp_path, f"REMOTE_CODE_BRIDGE_TOKEN={token}\n")
    with pytest.raises(BridgeError, match="exactly 64 ASCII hex"):
        read_host_config(path, no_env)
    with pytest.raises(BridgeError, match="exactly 64 ASCII hex"):
        read_remote_config(path, no_env)


def test_default_host_is_the_allowlist_when_none_is_configured(tmp_path):
    config = read_host_config(write(tmp_path, "REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox\n"), no_env)
    assert config.allowed_hosts == frozenset(["devbox"])
    assert config.tunnel_alias == "devbox"


def test_allowed_hosts_and_tunnel_settings(tmp_path):
    config = read_host_config(
        write(
            tmp_path,
            "REMOTE_CODE_BRIDGE_DEFAULT_HOST=devbox\nREMOTE_CODE_BRIDGE_ALLOWED_HOSTS= devbox, lab ,\n"
            "REMOTE_CODE_BRIDGE_DRY_RUN=yes\nREMOTE_CODE_BRIDGE_TUNNEL=0\nREMOTE_CODE_BRIDGE_TUNNEL_SOCKET=/s.sock\n",
        ),
        no_env,
    )
    assert config.allowed_hosts == frozenset(["devbox", "lab"])
    assert config.dry_run is True
    assert config.tunnel_enabled is False
    assert config.tunnel_socket == "/s.sock"


def test_repr_redacts_tokens():
    assert "secret" not in repr(HostConfig(token="secret"))
    assert "secret" not in repr(RemoteConfig(token="secret"))


def test_update_env_text_keeps_comments_and_unknown_keys():
    old = "# mine\nCUSTOM=1\nREMOTE_CODE_BRIDGE_TOKEN=old\n"
    new = update_env_text(old, {"REMOTE_CODE_BRIDGE_TOKEN": "new", "REMOTE_CODE_BRIDGE_PORT": "1"})
    assert new == "# mine\nCUSTOM=1\nREMOTE_CODE_BRIDGE_TOKEN=new\nREMOTE_CODE_BRIDGE_PORT=1\n"


def test_read_values_ignores_junk(tmp_path):
    assert read_values(write(tmp_path, "junk\n=x\nA = b=c \n")) == {"": "x", "A": "b=c"}


def test_generated_tokens_are_distinct_hex():
    first, second = generate_token(), generate_token()
    assert len(first) == 64 and int(first, 16) >= 0 and first != second


@pytest.mark.parametrize(
    "alias,valid",
    [
        ("devbox", True),
        ("lab.example-2", True),
        ("", False),
        ("-oProxyCommand=evil", False),
        ("bad alias", False),
        ("a*", False),
    ],
)
def test_alias_validation(alias, valid):
    assert is_valid_alias(alias) is valid
