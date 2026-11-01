import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "deploy" / "vps"
HEALTHY = '{"ok":true,"schema":{"ok":true,"version":"0002_jobs","latest":"0002_jobs"}}'
UNHEALTHY = '{"ok":false,"schema":{"ok":false,"version":"0001_baseline","latest":"0002_jobs"}}'


def _setup(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    host_dir = tmp_path / "host"
    host_dir.mkdir(exist_ok=True)
    log_file = tmp_path / "calls.log"

    docker = fake_bin / "docker"
    docker.write_text(
        """#!/bin/sh
printf 'docker\\t%s\\t' "${QUENYA_IMAGE_TAG-}" >> "$FAKE_LOG"
printf '%s ' "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
if [ "${1-}" = image ] && [ "${2-}" = inspect ]; then
    case ":${FAKE_IMAGES-}:" in
        *":$3:"*) exit 0 ;;
        *) exit 1 ;;
    esac
fi
if [ "${1-}" = image ] && [ "${2-}" = ls ]; then
    printf '%s\\n' "${FAKE_IMAGE_LIST-}"
    exit 0
fi
case "$*" in
    *"python -m app.migrate"*)
        if [ "${FAKE_MIGRATE_FAIL:-0}" = 1 ]; then
            exit 23
        fi
        ;;
esac
exit 0
"""
    )
    docker.chmod(0o755)

    curl = fake_bin / "curl"
    curl.write_text(
        """#!/bin/sh
printf 'curl\\t%s\\t' "${QUENYA_IMAGE_TAG-}" >> "$FAKE_LOG"
printf '%s ' "$@" >> "$FAKE_LOG"
printf '\\n' >> "$FAKE_LOG"
printf '%s\\n' "${FAKE_CURL_HEALTH}"
"""
    )
    curl.chmod(0o755)

    env = os.environ.copy()
    env.pop("QUENYA_RELEASE", None)
    env.pop("QUENYA_IMAGE_TAG", None)
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
            "QUENYA_DIR": str(host_dir),
            "QUENYA_HEALTH_TIMEOUT_S": "1",
            "QUENYA_HEALTH_POLL_INTERVAL_S": "0",
            "FAKE_CURL_HEALTH": HEALTHY,
            "FAKE_IMAGES": "",
            "FAKE_IMAGE_LIST": "",
            "FAKE_LOG": str(log_file),
        }
    )
    return env, host_dir, log_file


def _run(tmp_path, script, *args, overrides=None):
    env, host_dir, log_file = _setup(tmp_path)
    if overrides:
        env.update(overrides)
    result = subprocess.run(
        ["sh", str(SCRIPTS / script), *args],
        cwd=host_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result, host_dir, log_file


def _seed_release(host_dir, current, history):
    release_dir = host_dir / ".releases"
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / "current").write_text(f"{current}\n")
    (release_dir / "history").write_text("".join(f"{tag}\n" for tag in history))


def _docker_calls(log_file):
    return [
        (parts[1], parts[2])
        for line in log_file.read_text().splitlines()
        if line.startswith("docker\t")
        for parts in [line.split("\t", 2)]
    ]


def test_healthy_deploy_records_release_and_passes_image_tag(tmp_path):
    _, host_dir, _log_file = _setup(tmp_path)
    _seed_release(host_dir, "old-release", ["old-release"])
    result, _host_dir, log_file = _run(
        tmp_path,
        "deploy.sh",
        overrides={
            "QUENYA_RELEASE": "new-release",
            "FAKE_IMAGES": "quenya-quenya-api:old-release",
        },
    )

    assert result.returncode == 0, result.stderr
    assert (host_dir / ".releases" / "current").read_text() == "new-release\n"
    assert (host_dir / ".releases" / "history").read_text().splitlines() == [
        "old-release",
        "new-release",
    ]
    assert "Deployed new-release (previous: old-release)" in result.stdout
    calls = _docker_calls(log_file)
    assert any(
        tag == "new-release" and " compose " in f" {args} " and " build quenya-api" in args
        for tag, args in calls
    )
    assert any(
        tag == "new-release" and " run --rm --no-deps " in f" {args} "
        and "python -m app.migrate" in args
        for tag, args in calls
    )
    assert any(
        tag == "new-release" and " up -d quenya-api quenya-worker" in args
        for tag, args in calls
    )


def test_failed_migration_never_replaces_running_containers(tmp_path):
    _, host_dir, _log_file = _setup(tmp_path)
    _seed_release(host_dir, "old-release", ["old-release"])
    result, _host_dir, log_file = _run(
        tmp_path,
        "deploy.sh",
        overrides={
            "QUENYA_RELEASE": "new-release",
            "FAKE_MIGRATE_FAIL": "1",
        },
    )

    assert result.returncode != 0
    assert (host_dir / ".releases" / "current").read_text() == "old-release\n"
    assert (host_dir / ".releases" / "history").read_text().splitlines() == [
        "old-release"
    ]
    assert not any(
        " up -d quenya-api quenya-worker" in args
        for _tag, args in _docker_calls(log_file)
    )


def test_unhealthy_deploy_rolls_back_and_leaves_current_unchanged(tmp_path):
    _, host_dir, _log_file = _setup(tmp_path)
    _seed_release(host_dir, "old-release", ["old-release"])
    result, _host_dir, log_file = _run(
        tmp_path,
        "deploy.sh",
        overrides={
            "QUENYA_RELEASE": "new-release",
            "FAKE_IMAGES": "quenya-quenya-api:old-release",
            "FAKE_CURL_HEALTH": UNHEALTHY,
        },
    )

    assert result.returncode == 1
    assert (host_dir / ".releases" / "current").read_text() == "old-release\n"
    assert (host_dir / ".releases" / "history").read_text().splitlines() == [
        "old-release"
    ]
    up_tags = [
        tag
        for tag, args in _docker_calls(log_file)
        if " up -d quenya-api quenya-worker" in args
    ]
    assert up_tags == ["new-release", "old-release"]
    assert "rolling back to old-release" in result.stderr


def test_first_deploy_retags_latest_as_previous_release(tmp_path):
    result, host_dir, log_file = _run(
        tmp_path,
        "deploy.sh",
        overrides={
            "QUENYA_RELEASE": "first-release",
            "FAKE_IMAGES": "quenya-quenya-api:latest",
        },
    )

    assert result.returncode == 0, result.stderr
    assert (host_dir / ".releases" / "current").read_text() == "first-release\n"
    assert (host_dir / ".releases" / "history").read_text().splitlines() == [
        "pre-first-release",
        "first-release",
    ]
    assert any(
        "image tag quenya-quenya-api:latest quenya-quenya-api:pre-first-release"
        in args
        for _tag, args in _docker_calls(log_file)
    )


def test_rollback_without_arguments_selects_previous_history_entry(tmp_path):
    _, host_dir, _log_file = _setup(tmp_path)
    _seed_release(host_dir, "new-release", ["old-release", "new-release"])
    result, _host_dir, log_file = _run(
        tmp_path,
        "rollback.sh",
        overrides={
            "FAKE_IMAGES": (
                "quenya-quenya-api:old-release:quenya-quenya-api:new-release"
            )
        },
    )

    assert result.returncode == 0, result.stderr
    assert (host_dir / ".releases" / "current").read_text() == "old-release\n"
    assert (host_dir / ".releases" / "history").read_text().splitlines() == [
        "old-release",
        "new-release",
        "old-release",
    ]
    assert any(
        tag == "old-release" and " up -d quenya-api quenya-worker" in args
        for tag, args in _docker_calls(log_file)
    )


def test_rollback_list_prints_newest_first_and_marks_current(tmp_path):
    _, host_dir, _log_file = _setup(tmp_path)
    _seed_release(host_dir, "new-release", ["old-release", "new-release"])
    result, _host_dir, _log_file = _run(tmp_path, "rollback.sh", "--list")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["* new-release", "  old-release"]


def test_rollback_fails_when_target_image_is_missing(tmp_path):
    _env, host_dir, _log_file = _setup(tmp_path)
    _seed_release(host_dir, "new-release", ["old-release", "new-release"])
    result, _host_dir, log_file = _run(
        tmp_path,
        "rollback.sh",
        overrides={"FAKE_IMAGES": "quenya-quenya-api:new-release"},
    )

    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert not any(
        " up -d quenya-api quenya-worker" in args
        for _tag, args in _docker_calls(log_file)
    )


def test_invalid_release_tag_falls_back_to_timestamp(tmp_path):
    result, host_dir, log_file = _run(
        tmp_path,
        "deploy.sh",
        overrides={"QUENYA_RELEASE": "bad/release"},
    )

    assert result.returncode == 0, result.stderr
    tag = (host_dir / ".releases" / "current").read_text().strip()
    assert re.fullmatch(r"\d{14}", tag)
    for docker_tag, args in _docker_calls(log_file):
        if " build quenya-api" in args or "python -m app.migrate" in args:
            assert docker_tag == tag


def test_release_pruning_keeps_recent_current_previous_and_latest_images(tmp_path):
    _env, host_dir, _log_file = _setup(tmp_path)
    history = [f"release-{index}" for index in range(1, 7)]
    _seed_release(host_dir, history[-1], history)
    image_tags = [
        "release-1",
        "release-2",
        "release-3",
        "release-6",
        "release-7",
        "latest",
        "legacy",
    ]
    result, _host_dir, log_file = _run(
        tmp_path,
        "deploy.sh",
        overrides={
            "QUENYA_RELEASE": "release-7",
            "FAKE_IMAGE_LIST": "\n".join(
                f"quenya-quenya-api:{tag}" for tag in image_tags
            ),
        },
    )

    assert result.returncode == 0, result.stderr
    removed = [
        args.split()[-1].removeprefix("quenya-quenya-api:")
        for _tag, args in _docker_calls(log_file)
        if " image rm " in f" {args} "
    ]
    assert removed == ["release-1", "release-2", "legacy"]
