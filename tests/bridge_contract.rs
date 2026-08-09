use remote_code_bridge::{
    build_code_command, handle_request, HostConfig, HttpRequest, OpenRequest, Response,
};

fn host_config() -> HostConfig {
    HostConfig {
        bind: "127.0.0.1".into(),
        port: 39731,
        token: Some("token".into()),
        code_bin: "/usr/bin/code".into(),
        default_host: Some("devbox".into()),
        allowed_hosts: Some(["devbox".to_string()].into_iter().collect()),
        dry_run: true,
    }
}

fn open_request(body: serde_json::Value, authorization: &str) -> HttpRequest {
    HttpRequest {
        method: "POST".into(),
        path: "/open".into(),
        headers: [("authorization".into(), authorization.into())]
            .into_iter()
            .collect(),
        body: serde_json::to_vec(&body).unwrap(),
    }
}

#[test]
fn healthz_is_public_and_reports_service() {
    let response = handle_request(
        &host_config(),
        HttpRequest {
            method: "GET".into(),
            path: "/healthz".into(),
            headers: Default::default(),
            body: vec![],
        },
    );

    assert_eq!(response.status, 200);
    assert_eq!(
        response.json(),
        serde_json::json!({"ok": true, "service": "remote-code-bridge"})
    );
}

#[test]
fn open_requires_an_exact_bearer_token() {
    let response = handle_request(
        &host_config(),
        open_request(
            serde_json::json!({"path": "/srv/project"}),
            "Bearer incorrect",
        ),
    );

    assert_eq!(response, Response::error(401, "unauthorized"));
}

#[test]
fn dry_run_returns_the_safe_vscode_command() {
    let response = handle_request(
        &host_config(),
        open_request(
            serde_json::json!({
                "path": "/srv/project",
                "args": ["--reuse-window", "--bad", "-g"]
            }),
            "Bearer token",
        ),
    );

    assert_eq!(response.status, 200);
    assert_eq!(
        response.json(),
        serde_json::json!({
            "ok": true,
            "dry_run": true,
            "command": ["/usr/bin/code", "--reuse-window", "--remote", "ssh-remote+devbox", "-g", "/srv/project"]
        })
    );
}

#[test]
fn rejects_non_absolute_paths_and_unallowed_hosts() {
    let relative = handle_request(
        &host_config(),
        open_request(serde_json::json!({"path": "project"}), "Bearer token"),
    );
    let disallowed = handle_request(
        &host_config(),
        open_request(
            serde_json::json!({"path": "/srv/project", "host": "other"}),
            "Bearer token",
        ),
    );

    assert_eq!(
        relative,
        Response::error(400, "path must be an absolute remote path")
    );
    assert_eq!(
        disallowed,
        Response::error(403, "host alias not allowed: other")
    );
}

#[test]
fn rejects_non_concrete_host_aliases_control_paths_and_bodyful_health_checks() {
    let invalid_host = handle_request(
        &host_config(),
        open_request(
            serde_json::json!({"path": "/srv/project", "host": "-option"}),
            "Bearer token",
        ),
    );
    let control_path = handle_request(
        &host_config(),
        open_request(
            serde_json::json!({"path": "/srv/\u{0000}project"}),
            "Bearer token",
        ),
    );
    let bodyful_health = handle_request(
        &host_config(),
        HttpRequest {
            method: "GET".into(),
            path: "/healthz".into(),
            headers: Default::default(),
            body: b"nope".to_vec(),
        },
    );

    assert_eq!(invalid_host, Response::error(400, "invalid host alias"));
    assert_eq!(control_path, Response::error(400, "invalid remote path"));
    assert_eq!(bodyful_health, Response::error(400, "invalid request size"));
}

#[test]
fn command_builder_has_no_shell_expansion_or_unsafe_flags() {
    let request = OpenRequest {
        host: Some("devbox;rm -rf /".into()),
        path: "/safe path".into(),
        args: vec!["--new-window".into(), "--command=evil".into()],
    };

    assert_eq!(
        build_code_command(&host_config(), &request).unwrap(),
        vec![
            "/usr/bin/code",
            "--new-window",
            "--remote",
            "ssh-remote+devbox;rm -rf /",
            "/safe path"
        ]
    );
}
