#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "safe_deploy_verify.sh"
OLD_BASE_COMMIT = "8bd977a6347cb827378a05e3f28677a15eebf7f2"
NEW_MERGED_COMMIT = "f1e2d3c4b5a697887766554433221100ffeeddcc"
TARGET_BRANCH = "feature/candidate-validation-logs"
FEATURE_FLAG = "SCENARIO_LEDGER_ALERT_REGISTRATION_ENABLED"


def find_bash() -> str | None:
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "usr" / "bin" / "bash.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def bash_path(path: Path) -> str:
    posix = path.resolve().as_posix()
    if os.name == "nt" and len(posix) >= 3 and posix[1:3] == ":/":
        return f"/{posix[0].lower()}{posix[2:]}"
    return posix


class SafeDeployVerifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bash = find_bash()
        if cls.bash is None:
            raise unittest.SkipTest("bash is required for safe deployment script tests")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="bitswipe-safe-deploy-")
        self.test_root = Path(self.tmp.name)
        self.mock_bin = self.test_root / "mock-bin"
        self.mock_bin.mkdir()
        self.command_log = self.test_root / "commands.log"
        self.mock_head = self.test_root / "mock-head.txt"
        self.mock_head.write_text(OLD_BASE_COMMIT, encoding="ascii", newline="\n")
        self.database = self.test_root / "data" / "scenario_ledger.sqlite3"
        self.database.parent.mkdir()
        self.database.write_bytes(b"database-sentinel")
        self.backup = self.test_root / "untracked-config.bak"
        self.backup.write_text("must remain untouched", encoding="utf-8")
        self.dotenv = self.test_root / ".env"
        self.dotenv.write_text(f"{FEATURE_FLAG}=0\n", encoding="utf-8", newline="\n")
        self.harness = self.test_root / "harness.sh"
        self.harness.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                source "$SCRIPT_UNDER_TEST"
                git() { bash "$MOCK_BIN/git" "$@"; }
                systemctl() { bash "$MOCK_BIN/systemctl" "$@"; }
                journalctl() { bash "$MOCK_BIN/journalctl" "$@"; }
                sudo() { bash "$MOCK_BIN/sudo" "$@"; }
                curl() { bash "$MOCK_BIN/curl" "$@"; }
                python3() { bash "$MOCK_BIN/python3" "$@"; }
                cd() {
                    if [[ "${1:-}" == "--" && "${2:-}" == "/opt/bitswipe" ]]; then
                        builtin cd -- "$TEST_ROOT"
                    else
                        builtin cd "$@"
                    fi
                }
                main "$@"
                """
            ),
            encoding="utf-8",
            newline="\n",
        )
        self.harness.chmod(0o755)
        self._write_mocks()

    def tearDown(self):
        self.tmp.cleanup()

    def write_mock(self, name: str, body: str):
        path = self.mock_bin / name
        path.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s' '" + name + "' >>\"$MOCK_LOG\"\n"
            "printf ' %q' \"$@\" >>\"$MOCK_LOG\"\n"
            "printf '\\n' >>\"$MOCK_LOG\"\n"
            + textwrap.dedent(body),
            encoding="utf-8",
            newline="\n",
        )
        path.chmod(0o755)

    def _write_mocks(self):
        self.write_mock(
            "git",
            """
            case "${1:-}" in
                diff)
                    if [[ "${2:-}" == "--cached" ]]; then
                        [[ "${MOCK_STAGED_DIRTY:-0}" != "1" ]]
                    else
                        [[ "${MOCK_DIRTY:-0}" != "1" ]]
                    fi
                    ;;
                fetch)
                    [[ "${2:-}" == "origin" ]]
                    ;;
                rev-parse)
                    case "${2:-}" in
                        --show-toplevel) printf '%s\\n' "$TEST_ROOT" ;;
                        --verify) printf '%s\\n' "${MOCK_REMOTE_COMMIT:-$NEW_MERGED_COMMIT}" ;;
                        HEAD) /usr/bin/cat "$MOCK_HEAD_FILE" ;;
                        *) exit 2 ;;
                    esac
                    ;;
                branch)
                    [[ "${2:-}" == "--show-current" ]] || exit 2
                    printf '%s\\n' "${MOCK_BRANCH:-feature/candidate-validation-logs}"
                    ;;
                merge-base)
                    [[ "${MOCK_DIVERGED:-0}" != "1" ]]
                    ;;
                merge)
                    [[ "${1:-}" == "merge" && "${2:-}" == "--ff-only" ]]
                    printf '%s\\n' "${MOCK_REMOTE_COMMIT:-$NEW_MERGED_COMMIT}" >"$MOCK_HEAD_FILE"
                    ;;
                *) exit 2 ;;
            esac
            """,
        )
        self.write_mock(
            "systemctl",
            """
            case "${1:-}" in
                show)
                    case " $* " in
                        *" --property=Environment "*) printf '%s\\n' "${MOCK_UNIT_ENVIRONMENT:-LOG_LEVEL=INFO}" ;;
                        *" --property=MainPID "*) printf '%s\\n' "${MOCK_MAIN_PID:-4242}" ;;
                        *) exit 2 ;;
                    esac
                    ;;
                cat)
                    printf '%b' "${MOCK_UNIT_TEXT:-[Service]\\nEnvironment=LOG_LEVEL=INFO\\n}"
                    ;;
                is-active)
                    unit="${3:-}"
                    if [[ "$unit" == "bitswipe.service" && "${MOCK_SERVICE_INACTIVE:-0}" == "1" ]]; then
                        exit 3
                    fi
                    if [[ "$unit" == "bitswipe-btc-watch.timer" && "${MOCK_TIMER_INACTIVE:-0}" == "1" ]]; then
                        exit 3
                    fi
                    ;;
                restart)
                    [[ "${2:-}" == "bitswipe.service" ]]
                    ;;
                *) exit 2 ;;
            esac
            """,
        )
        self.write_mock(
            "journalctl",
            """
            case " $* " in
                *" --show-cursor "*) printf '%s\\n' '-- cursor: mock-cursor' ;;
                *)
                    if [[ "${MOCK_FATAL_LOG:-0}" == "1" ]]; then
                        printf '%s\\n' 'Traceback (most recent call last): startup failed'
                    else
                        printf '%s\\n' 'Binance user-data stream returned 401; using REST fallback'
                    fi
                    ;;
            esac
            """,
        )
        self.write_mock(
            "sudo",
            f"""
            if [[ "${{1:-}}" == "cat" ]]; then
                shift
                [[ "${{1:-}}" == "--" ]] && shift
                requested_path="${{1:-}}"
                if [[ "$requested_path" == /proc/*/environ ]]; then
                    if [[ "${{MOCK_PROCESS_FLAG_ENABLED:-0}}" == "1" ]]; then
                        printf 'PATH=/usr/bin\\0{FEATURE_FLAG}=true\\0'
                    else
                        printf 'PATH=/usr/bin\\0{FEATURE_FLAG}=0\\0'
                    fi
                    exit 0
                fi
                [[ "${{MOCK_CAT_FAIL_PATH:-}}" != "$requested_path" ]] || exit 1
                /usr/bin/cat -- "$requested_path"
                exit
            fi
            if [[ "${{1:-}}" == "bash" ]]; then
                shift
                /usr/bin/bash "$@"
                exit
            fi
            nested="$1"
            shift
            bash "$MOCK_BIN/$nested" "$@"
            """,
        )
        self.write_mock(
            "curl",
            """
            case " $* " in
                *" /api/connections "*)
                    if [[ "${MOCK_BAD_JSON:-0}" == "1" ]]; then
                        printf '%s\\n' 'not-json'
                    else
                        printf '%s\\n' '{"connections": []}'
                    fi
                    ;;
                *) printf '%s' "${MOCK_HTTP_CODE:-200}" ;;
            esac
            """,
        )
        self.write_mock(
            "python3",
            """
            if [[ "${1:-}" == "-c" ]]; then
                cat >/dev/null
                [[ "${MOCK_BAD_JSON:-0}" != "1" ]]
                exit
            fi
            cat >/dev/null
            printf '%s\\n' '데이터베이스 무결성: ok' '시나리오 수: 3' '이벤트 수: 7'
            """,
        )

    def run_script(self, *args: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        initial_head = overrides.pop("MOCK_INITIAL_HEAD", OLD_BASE_COMMIT)
        self.mock_head.write_text(initial_head, encoding="ascii", newline="\n")
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bash_path(self.mock_bin)}:/usr/bin:/bin",
                "MOCK_BIN": bash_path(self.mock_bin),
                "SCRIPT_UNDER_TEST": bash_path(SCRIPT),
                "TEST_ROOT": bash_path(self.test_root),
                "MOCK_LOG": bash_path(self.command_log),
                "MOCK_HEAD_FILE": bash_path(self.mock_head),
                "NEW_MERGED_COMMIT": NEW_MERGED_COMMIT,
            }
        )
        env.update(overrides)
        return subprocess.run(
            [self.bash, self.harness.as_posix(), *args],
            cwd=self.test_root,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )

    def run_with_expected(
        self,
        *args: str,
        expected_commit: str = NEW_MERGED_COMMIT,
        **overrides: str,
    ) -> subprocess.CompletedProcess[str]:
        return self.run_script(
            "--expected-commit", expected_commit, *args, **overrides
        )

    def commands(self) -> str:
        if not self.command_log.exists():
            return ""
        return self.command_log.read_text(encoding="utf-8")

    def assert_sentinels_unchanged(self):
        self.assertEqual(self.backup.read_text(encoding="utf-8"), "must remain untouched")
        self.assertEqual(self.database.read_bytes(), b"database-sentinel")
        self.assertEqual(self.dotenv.read_text(encoding="utf-8"), f"{FEATURE_FLAG}=0\n")

    def assert_no_prohibited_commands(self, command_log: str):
        for prohibited in (
            "git reset",
            "git clean",
            "checkout -f",
            "push --force",
            "bitswipe-btc-watch.timer restart",
            "bitswipe-btc-watch.timer stop",
            "telegram",
            "order",
            ".env",
        ):
            self.assertNotIn(prohibited, command_log.lower())

    def test_full_post_merge_lifecycle_fast_forwards_old_base_to_explicit_new_commit(self):
        result = self.run_with_expected()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("성공: BitSwipe 배포 검증을 모두 통과했습니다.", result.stdout)
        self.assertEqual(self.mock_head.read_text(encoding="ascii").strip(), NEW_MERGED_COMMIT)
        commands = self.commands()
        self.assertIn("git fetch origin", commands)
        self.assertIn(f"git merge --ff-only origin/{TARGET_BRANCH}", commands)
        self.assertIn("sudo systemctl restart bitswipe.service", commands)
        self.assertEqual(
            commands.count("systemctl is-active --quiet bitswipe-btc-watch.timer"), 2
        )
        self.assertIn("--after-cursor=mock-cursor", commands)
        curl_commands = [line for line in commands.splitlines() if line.startswith("curl ")]
        self.assertTrue(curl_commands)
        self.assertTrue(all("127.0.0.1:8000" in line for line in curl_commands))
        self.assert_no_prohibited_commands(commands)
        self.assert_sentinels_unchanged()

    def test_check_only_skips_merge_and_restart_but_runs_checks(self):
        result = self.run_with_expected(
            "--check-only", MOCK_INITIAL_HEAD=NEW_MERGED_COMMIT
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("검사 전용 모드", result.stdout)
        commands = self.commands()
        self.assertNotIn("git merge ", commands)
        self.assertNotIn("systemctl restart", commands)
        self.assertIn("systemctl show bitswipe.service --property=MainPID --value", commands)
        self.assertIn("curl", commands)
        self.assertIn("journalctl", commands)
        self.assert_sentinels_unchanged()

    def test_dirty_tracked_file_fails_before_fetch_or_restart_and_ignores_bak(self):
        result = self.run_with_expected(MOCK_DIRTY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("추적 중인 파일", result.stderr)
        commands = self.commands()
        self.assertNotIn("git fetch", commands)
        self.assertNotIn("systemctl restart", commands)
        self.assert_sentinels_unchanged()

    def test_staged_tracked_file_also_fails_before_fetch(self):
        result = self.run_with_expected(MOCK_STAGED_DIRTY="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("스테이징된 추적 파일", result.stderr)
        self.assertNotIn("git fetch", self.commands())
        self.assert_sentinels_unchanged()

    def test_missing_or_invalid_expected_commit_fails_before_any_action(self):
        for args in ((), ("--check-only",), ("--expected-commit", "not-a-sha")):
            with self.subTest(args=args):
                self.command_log.unlink(missing_ok=True)
                result = self.run_script(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("git fetch", self.commands())
                self.assertNotIn("git merge", self.commands())
                self.assertNotIn("systemctl restart", self.commands())
                self.assert_sentinels_unchanged()

    def test_stale_old_expected_commit_fails_before_merge_or_restart(self):
        result = self.run_script("--expected-commit", OLD_BASE_COMMIT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("원격 브랜치가 예상 커밋과 다릅니다", result.stderr)
        commands = self.commands()
        self.assertNotIn("git merge ", commands)
        self.assertNotIn("systemctl restart", commands)

    def test_enabled_systemd_flag_fails_before_restart(self):
        result = self.run_with_expected(MOCK_UNIT_ENVIRONMENT=f'{FEATURE_FLAG}="yes"')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("systemd Environment", result.stderr)
        self.assertNotIn("systemctl restart", self.commands())

    def test_enabled_flag_in_unit_text_fails_before_restart(self):
        result = self.run_with_expected(
            MOCK_UNIT_TEXT=f'[Service]\nEnvironment="{FEATURE_FLAG}=on"\n'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unit/drop-in", result.stderr)
        self.assertNotIn("systemctl restart", self.commands())

    def test_configured_environment_files_are_read_only_and_optional_missing_is_safe(self):
        environment_dir = self.test_root / "systemd-env"
        environment_dir.mkdir()
        first = environment_dir / "a.conf"
        second = environment_dir / "b.conf"
        spaced = self.test_root / "service environment.conf"
        missing_optional = self.test_root / "missing-optional.conf"
        first.write_text(f"{FEATURE_FLAG}=0\n", encoding="utf-8", newline="\n")
        second.write_text("LOG_LEVEL=INFO\n", encoding="utf-8", newline="\n")
        spaced.write_text(f"{FEATURE_FLAG}=false\n", encoding="utf-8", newline="\n")
        unit_text = (
            "[Service]\n"
            f"EnvironmentFile={bash_path(environment_dir)}/*.conf\n"
            f"EnvironmentFile=-{bash_path(missing_optional)}\n"
            f'EnvironmentFile="{bash_path(spaced)}"\n'
        )

        result = self.run_with_expected(MOCK_UNIT_TEXT=unit_text)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = self.commands()
        for configured_file in (first, second, spaced, missing_optional):
            self.assertIn(configured_file.name.replace(" ", "\\ "), commands)
        self.assertNotIn(bash_path(self.dotenv), commands)
        self.assertEqual(first.read_text(encoding="utf-8"), f"{FEATURE_FLAG}=0\n")
        self.assertEqual(second.read_text(encoding="utf-8"), "LOG_LEVEL=INFO\n")
        self.assertEqual(spaced.read_text(encoding="utf-8"), f"{FEATURE_FLAG}=false\n")
        self.assert_sentinels_unchanged()

    def test_required_environment_file_inspection_failure_stops_before_restart(self):
        required = self.test_root / "required.conf"
        required.write_text("LOG_LEVEL=INFO\n", encoding="utf-8", newline="\n")
        result = self.run_with_expected(
            MOCK_UNIT_TEXT=f"[Service]\nEnvironmentFile={bash_path(required)}\n",
            MOCK_CAT_FAIL_PATH=bash_path(required),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("필수 EnvironmentFile", result.stderr)
        self.assertNotIn("systemctl restart", self.commands())
        self.assert_sentinels_unchanged()

    def test_environment_file_enabled_flag_stops_before_restart(self):
        environment_file = self.test_root / "enabled.conf"
        enabled = f"{FEATURE_FLAG}=true\n"
        environment_file.write_text(enabled, encoding="utf-8", newline="\n")
        result = self.run_with_expected(
            MOCK_UNIT_TEXT=(
                f"[Service]\nEnvironmentFile={bash_path(environment_file)}\n"
            )
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(FEATURE_FLAG, result.stderr)
        self.assertNotIn("systemctl restart", self.commands())
        self.assertEqual(environment_file.read_text(encoding="utf-8"), enabled)

    def test_environment_file_reset_reads_only_effective_configuration(self):
        superseded = self.test_root / "superseded.conf"
        effective = self.test_root / "effective.conf"
        superseded.write_text(f"{FEATURE_FLAG}=true\n", encoding="utf-8", newline="\n")
        effective.write_text(f"{FEATURE_FLAG}=0\n", encoding="utf-8", newline="\n")
        unit_text = (
            "[Service]\n"
            f"EnvironmentFile={bash_path(superseded)}\n"
            "EnvironmentFile=\n"
            f"EnvironmentFile={bash_path(effective)}\n"
        )
        result = self.run_with_expected(MOCK_UNIT_TEXT=unit_text)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = self.commands()
        self.assertNotIn(superseded.name, commands)
        self.assertIn(effective.name, commands)

    def test_enabled_process_flag_fails_closed_after_service_restart(self):
        result = self.run_with_expected(MOCK_PROCESS_FLAG_ENABLED="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("실행 중인 프로세스", result.stderr)
        commands = self.commands()
        self.assertIn("sudo systemctl restart bitswipe.service", commands)
        self.assertNotIn("bitswipe-btc-watch.timer restart", commands)

    def test_fatal_startup_log_fails_but_binance_401_does_not(self):
        normal = self.run_with_expected()
        self.assertEqual(normal.returncode, 0, normal.stdout + normal.stderr)

        self.command_log.unlink(missing_ok=True)
        fatal = self.run_with_expected(MOCK_FATAL_LOG="1")
        self.assertNotEqual(fatal.returncode, 0)
        self.assertIn("치명적 시작 오류", fatal.stderr)
        self.assert_sentinels_unchanged()

    def test_zero_main_pid_and_invalid_json_fail_closed(self):
        zero_pid = self.run_with_expected(MOCK_MAIN_PID="0")
        self.assertNotEqual(zero_pid.returncode, 0)
        self.assertIn("MainPID가 0", zero_pid.stderr)

        self.command_log.unlink(missing_ok=True)
        bad_json = self.run_with_expected(MOCK_BAD_JSON="1")
        self.assertNotEqual(bad_json.returncode, 0)
        self.assertIn("올바른 JSON", bad_json.stderr)
        self.assert_sentinels_unchanged()

    def test_expected_commit_override_is_validated_and_used(self):
        custom = "abcdef0123456789abcdef0123456789abcdef01"
        result = self.run_script(
            "--check-only",
            "--expected-commit",
            custom,
            MOCK_REMOTE_COMMIT=custom,
            MOCK_INITIAL_HEAD=custom,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(custom, result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
