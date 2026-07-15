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


def find_bash() -> str | None:
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Git"
        / "bin"
        / "bash.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Git"
        / "usr"
        / "bin"
        / "bash.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


class SafeDeploySystemctlCatPermissionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bash = find_bash()
        if cls.bash is None:
            raise unittest.SkipTest("bash is required for the systemctl permission test")

    def test_protected_unit_is_read_through_sudo(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bitswipe-systemctl-cat-") as tmp:
            tmp_path = Path(tmp)
            command_log = tmp_path / "commands.log"
            harness = tmp_path / "harness.sh"
            harness.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    source "$SCRIPT_UNDER_TEST"

                    log_command() {
                        local name="$1"
                        shift
                        printf '%s' "$name" >>"$COMMAND_LOG"
                        printf ' %q' "$@" >>"$COMMAND_LOG"
                        printf '\n' >>"$COMMAND_LOG"
                    }

                    systemctl() {
                        log_command systemctl "$@"
                        case "${1:-}" in
                            show)
                                [[ "${2:-}" == "bitswipe.service" ]]
                                [[ "${3:-}" == "--property=Environment" ]]
                                [[ "${4:-}" == "--value" ]]
                                printf '%s\n' 'LOG_LEVEL=INFO'
                                ;;
                            cat)
                                printf '%s\n' 'direct systemctl cat is permission denied' >&2
                                return 77
                                ;;
                            *)
                                return 78
                                ;;
                        esac
                    }

                    sudo() {
                        log_command sudo "$@"
                        [[ "${1:-}" == "systemctl" ]]
                        [[ "${2:-}" == "cat" ]]
                        [[ "${3:-}" == "bitswipe.service" ]]
                        [[ "${4:-}" == "--no-pager" ]]
                        printf '%s\n' \
                            '[Service]' \
                            'Environment=LOG_LEVEL=INFO'
                    }

                    verify_systemd_flag_disabled
                    """
                ),
                encoding="utf-8",
                newline="\n",
            )
            harness.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "SCRIPT_UNDER_TEST": SCRIPT.resolve().as_posix(),
                    "COMMAND_LOG": command_log.resolve().as_posix(),
                }
            )
            result = subprocess.run(
                [self.bash, harness.as_posix()],
                cwd=ROOT,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            commands = command_log.read_text(encoding="utf-8")
            self.assertIn(
                "sudo systemctl cat bitswipe.service --no-pager",
                commands,
            )
            self.assertNotIn(
                "\nsystemctl cat bitswipe.service --no-pager\n",
                "\n" + commands,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
