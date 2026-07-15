#!/usr/bin/env bash

set -Eeuo pipefail

readonly APP_DIR="/opt/bitswipe"
readonly TARGET_BRANCH="feature/candidate-validation-logs"
readonly SERVICE_NAME="bitswipe.service"
readonly WATCH_TIMER_NAME="bitswipe-btc-watch.timer"
readonly FEATURE_FLAG="SCENARIO_LEDGER_ALERT_REGISTRATION_ENABLED"
readonly DATABASE_PATH="data/scenario_ledger.sqlite3"
readonly FATAL_LOG_PATTERN='Traceback|SyntaxError|ImportError|ModuleNotFoundError|Failed to start'
readonly ENV_PARSER_PATH="scripts/safe_deploy_verify_env.py"
readonly ENV_PARSER_MISSING_EXIT=40

CHECK_ONLY=0
EXPECTED_COMMIT=""
CURRENT_STEP="인수 확인"
FAILURE_REASON=""
SUCCESS=0
MAIN_PID=""
RESTART_CURSOR=""
declare -a ENVIRONMENT_FILE_PATHS=()
declare -a ENVIRONMENT_FILE_OPTIONAL=()

usage() {
    printf '%s\n' \
        '사용법: bash scripts/safe_deploy_verify.sh --expected-commit <40자리 SHA> [--check-only]' \
        '' \
        "  --expected-commit SHA origin/${TARGET_BRANCH}에 있어야 할 검토된 커밋입니다. 모든 실행에서 필수입니다." \
        '  --check-only          같은 SHA를 확인하되 git 병합과 서비스 재시작은 하지 않습니다.' \
        '  -h, --help            이 도움말을 표시합니다.'
}

fail() {
    FAILURE_REASON="$1"
    printf '\n오류: %s\n' "$FAILURE_REASON" >&2
    exit 1
}

print_exit_summary() {
    local status="$1"

    trap - EXIT
    if [[ "$status" -eq 0 && "$SUCCESS" -eq 1 ]]; then
        printf '\n============================================================\n'
        printf '성공: BitSwipe 배포 검증을 모두 통과했습니다.\n'
        printf '확인한 커밋: %s\n' "$EXPECTED_COMMIT"
        if [[ "$CHECK_ONLY" -eq 1 ]]; then
            printf '검사 전용 모드이므로 git 병합과 서비스 재시작은 하지 않았습니다.\n'
        else
            printf '%s만 안전하게 재시작했고 정상 동작을 확인했습니다.\n' "$SERVICE_NAME"
        fi
        printf '%s는 계속 active 상태입니다.\n' "$WATCH_TIMER_NAME"
        printf 'Scenario Ledger 기능 플래그는 활성화되어 있지 않습니다.\n'
        printf '============================================================\n'
        return
    fi

    printf '\n============================================================\n' >&2
    printf '실패: 안전 검증을 완료하지 못했습니다.\n' >&2
    printf '실패한 단계: %s\n' "$CURRENT_STEP" >&2
    if [[ -n "$FAILURE_REASON" ]]; then
        printf '이유: %s\n' "$FAILURE_REASON" >&2
    else
        printf '이유: 명령이 종료 코드 %s로 실패했습니다. 위 오류를 확인하세요.\n' "$status" >&2
    fi
    printf '안전을 위해 남은 단계는 중단했습니다. 운영자가 원인을 확인한 뒤 다시 실행하세요.\n' >&2
    printf '============================================================\n' >&2
}

parse_args() {
    while [[ "$#" -gt 0 ]]; do
        case "$1" in
            --check-only)
                CHECK_ONLY=1
                shift
                ;;
            --expected-commit)
                [[ "$#" -ge 2 ]] || fail "--expected-commit 뒤에 40자리 커밋 SHA가 필요합니다."
                EXPECTED_COMMIT="$2"
                shift 2
                ;;
            -h|--help)
                trap - EXIT
                usage
                exit 0
                ;;
            *)
                fail "알 수 없는 옵션입니다: $1"
                ;;
        esac
    done

    [[ -n "$EXPECTED_COMMIT" ]] || \
        fail "안전을 위해 --expected-commit <40자리 SHA>를 모든 실행에서 반드시 지정해야 합니다."
    [[ "$EXPECTED_COMMIT" =~ ^[0-9a-fA-F]{40}$ ]] || \
        fail "예상 커밋은 정확한 40자리 16진수 SHA여야 합니다."
    EXPECTED_COMMIT="${EXPECTED_COMMIT,,}"
}

flag_enabled_in_text() {
    local text="$1"
    local pattern
    pattern="(^|[[:space:]\"'])${FEATURE_FLAG}[[:space:]]*=[[:space:]]*[\"']?(1|true|yes|on)([[:space:]\"']|$)"
    LC_ALL=C grep -Eiq -- "$pattern" <<<"$text"
}

verify_tracked_files_clean() {
    if ! git diff --quiet --ignore-submodules --; then
        fail "추적 중인 파일에 커밋되지 않은 변경이 있습니다. 먼저 변경을 확인하고 커밋하거나 보관하세요."
    fi
    if ! git diff --cached --quiet --ignore-submodules --; then
        fail "스테이징된 추적 파일 변경이 있습니다. 먼저 변경을 확인하고 커밋하거나 보관하세요."
    fi
}

enter_repository() {
    local current_root repo_root repo_root_physical

    cd -- "$APP_DIR" || fail "$APP_DIR 디렉터리로 이동할 수 없습니다."
    current_root="$(pwd -P)"
    repo_root="$(git rev-parse --show-toplevel)" || fail "$APP_DIR가 Git 저장소가 아닙니다."
    repo_root_physical="$(cd -- "$repo_root" && pwd -P)"
    [[ "$current_root" == "$repo_root_physical" ]] || \
        fail "$APP_DIR 자체가 Git 저장소 최상위 디렉터리가 아닙니다."
}

verify_and_update_git() {
    local remote_commit current_branch head_commit

    CURRENT_STEP="Git 추적 파일 상태 확인"
    verify_tracked_files_clean

    CURRENT_STEP="origin 가져오기"
    git fetch origin

    CURRENT_STEP="원격 대상 커밋 확인"
    remote_commit="$(git rev-parse --verify "origin/${TARGET_BRANCH}^{commit}")" || \
        fail "origin/${TARGET_BRANCH} 커밋을 읽을 수 없습니다."
    remote_commit="${remote_commit,,}"
    [[ "$remote_commit" == "$EXPECTED_COMMIT" ]] || \
        fail "원격 브랜치가 예상 커밋과 다릅니다. 예상: $EXPECTED_COMMIT, 실제: $remote_commit"

    current_branch="$(git branch --show-current)"
    [[ "$current_branch" == "$TARGET_BRANCH" ]] || \
        fail "현재 브랜치가 $TARGET_BRANCH가 아닙니다. 실제: ${current_branch:-detached HEAD}"

    if [[ "$CHECK_ONLY" -eq 0 ]]; then
        CURRENT_STEP="fast-forward 가능 여부 확인"
        if ! git merge-base --is-ancestor HEAD "origin/${TARGET_BRANCH}"; then
            fail "현재 브랜치를 원격 대상까지 fast-forward할 수 없습니다. 수동 확인이 필요합니다."
        fi

        CURRENT_STEP="대상 브랜치 fast-forward"
        git merge --ff-only "origin/${TARGET_BRANCH}"
    fi

    CURRENT_STEP="현재 체크아웃 커밋 확인"
    head_commit="$(git rev-parse HEAD)" || fail "현재 커밋을 읽을 수 없습니다."
    head_commit="${head_commit,,}"
    [[ "$head_commit" == "$EXPECTED_COMMIT" ]] || \
        fail "현재 체크아웃이 예상 커밋이 아닙니다. 예상: $EXPECTED_COMMIT, 실제: $head_commit"
}

verify_systemd_flag_disabled() {
    local configured_environment unit_text line trimmed

    CURRENT_STEP="systemd 기능 플래그 확인"
    configured_environment="$(systemctl show "$SERVICE_NAME" --property=Environment --value)" || \
        fail "$SERVICE_NAME의 systemd 환경 설정을 읽을 수 없습니다."
    if flag_enabled_in_text "$configured_environment"; then
        fail "systemd Environment 설정에서 $FEATURE_FLAG가 활성화되어 있습니다."
    fi

    unit_text="$(sudo systemctl cat "$SERVICE_NAME" --no-pager)" || \
        fail "$SERVICE_NAME의 unit/drop-in 설정을 읽을 수 없습니다."
    while IFS= read -r line; do
        trimmed="${line#"${line%%[![:space:]]*}"}"
        case "$trimmed" in
            ""|\#*|\;*) continue ;;
        esac
        if flag_enabled_in_text "$trimmed"; then
            fail "systemd unit/drop-in 설정에서 $FEATURE_FLAG가 활성화되어 있습니다."
        fi
    done <<<"$unit_text"

    parse_environment_file_directives "$unit_text"
    inspect_environment_files
}

trim_whitespace() {
    local value="$1"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    printf '%s' "$value"
}

add_environment_file_spec() {
    local value optional=0 quoted=0 quote_character

    value="$(trim_whitespace "$1")"
    if [[ -z "$value" ]]; then
        ENVIRONMENT_FILE_PATHS=()
        ENVIRONMENT_FILE_OPTIONAL=()
        return
    fi
    [[ "$value" != *'\' ]] || \
        fail "여러 줄 EnvironmentFile 설정은 안전하게 해석할 수 없어 중단합니다: $value"

    if [[ "$value" == -* ]]; then
        optional=1
        value="$(trim_whitespace "${value#-}")"
    fi

    quote_character="${value:0:1}"
    if [[ "$quote_character" == '"' || "$quote_character" == "'" ]]; then
        quoted=1
        [[ "${value: -1}" == "$quote_character" && "${#value}" -ge 2 ]] || \
            fail "EnvironmentFile 따옴표가 올바르게 닫히지 않았습니다: $value"
        value="${value:1:${#value}-2}"
    fi

    if [[ "$optional" -eq 0 && "$value" == -* ]]; then
        optional=1
        value="${value#-}"
    fi

    [[ -n "$value" ]] || fail "EnvironmentFile 경로가 비어 있습니다."
    [[ "$value" == /* ]] || fail "EnvironmentFile 경로가 절대 경로가 아닙니다: $value"
    [[ "$value" != *'%'* ]] || \
        fail "systemd specifier가 포함된 EnvironmentFile 경로는 안전하게 검사할 수 없습니다: $value"
    [[ "$value" != *'\'* ]] || \
        fail "이스케이프가 포함된 EnvironmentFile 경로는 안전하게 검사할 수 없습니다: $value"
    if [[ "$quoted" -eq 0 && "$value" =~ [[:space:]] ]]; then
        fail "따옴표 없는 EnvironmentFile 경로에 공백이 있습니다: $value"
    fi

    ENVIRONMENT_FILE_PATHS+=("$value")
    ENVIRONMENT_FILE_OPTIONAL+=("$optional")
}

parse_environment_file_directives() {
    local unit_text="$1" line trimmed section=""

    ENVIRONMENT_FILE_PATHS=()
    ENVIRONMENT_FILE_OPTIONAL=()
    while IFS= read -r line; do
        trimmed="$(trim_whitespace "$line")"
        case "$trimmed" in
            ""|\#*|\;*) continue ;;
            \[*\])
                section="$trimmed"
                continue
                ;;
        esac
        [[ "$section" == "[Service]" ]] || continue
        if [[ "$trimmed" =~ ^EnvironmentFile[[:space:]]*=(.*)$ ]]; then
            add_environment_file_spec "${BASH_REMATCH[1]}"
        fi
    done <<<"$unit_text"
}

inspect_environment_file_spec() {
    local path_pattern="$1" optional="$2" environment_file glob_output
    local parser_output parser_status
    local -a matched_files=()

    if [[ "$path_pattern" == *'*'* || "$path_pattern" == *'?'* || "$path_pattern" == *'['* ]]; then
        if ! glob_output="$(sudo bash -c 'compgen -G "$1" || true' _ "$path_pattern")"; then
            fail "EnvironmentFile 와일드카드를 root 권한으로 검사할 수 없습니다: $path_pattern"
        fi
        while IFS= read -r environment_file; do
            [[ -n "$environment_file" ]] && matched_files+=("$environment_file")
        done <<<"$glob_output"
    else
        matched_files+=("$path_pattern")
    fi

    if [[ "${#matched_files[@]}" -eq 0 ]]; then
        if [[ "$optional" -eq 1 ]]; then
            printf '선택적 EnvironmentFile이 없어 건너뜁니다: %s\n' "$path_pattern"
            return
        fi
        fail "필수 EnvironmentFile을 찾을 수 없습니다: $path_pattern"
    fi

    for environment_file in "${matched_files[@]}"; do
        if parser_output="$(sudo python3 -I -S -X utf8 "$ENV_PARSER_PATH" \
            environment-file "$environment_file" 2>&1)"; then
            continue
        else
            parser_status="$?"
            if [[ "$parser_status" -eq "$ENV_PARSER_MISSING_EXIT" ]]; then
                if [[ "$optional" -eq 1 ]]; then
                    printf '선택적 EnvironmentFile이 없어 systemd 의미에 따라 건너뜁니다: %s\n' "$environment_file"
                    continue
                fi
                fail "필수 EnvironmentFile을 읽기 전용으로 검사할 수 없습니다: $environment_file. ${parser_output:-자세한 오류 없음}"
            fi
            fail "$environment_file 검사가 안전하게 완료되지 않았습니다. 파일은 수정하지 않았습니다. ${parser_output:-자세한 오류 없음}"
        fi
    done
}

inspect_environment_files() {
    local index

    CURRENT_STEP="systemd EnvironmentFile 읽기 전용 확인"
    for index in "${!ENVIRONMENT_FILE_PATHS[@]}"; do
        inspect_environment_file_spec \
            "${ENVIRONMENT_FILE_PATHS[$index]}" \
            "${ENVIRONMENT_FILE_OPTIONAL[$index]}"
    done
}

verify_unit_active() {
    local unit_name="$1"
    local beginner_name="$2"

    if ! systemctl is-active --quiet "$unit_name"; then
        fail "$beginner_name($unit_name)가 active 상태가 아닙니다."
    fi
}

capture_restart_cursor() {
    local cursor_output cursor_line

    CURRENT_STEP="재시작 전 로그 기준점 저장"
    cursor_output="$(sudo journalctl --unit="$SERVICE_NAME" --lines=0 --show-cursor --no-pager)" || \
        fail "$SERVICE_NAME 로그 기준점을 읽을 수 없습니다."
    cursor_line="${cursor_output##*$'\n'}"
    [[ "$cursor_line" == "-- cursor: "* ]] || \
        fail "journalctl 로그 기준점을 찾을 수 없습니다."
    RESTART_CURSOR="${cursor_line#-- cursor: }"
    [[ -n "$RESTART_CURSOR" ]] || fail "journalctl 로그 기준점이 비어 있습니다."
}

restart_service_if_requested() {
    CURRENT_STEP="BTC watch timer 재시작 전 상태 확인"
    verify_unit_active "$WATCH_TIMER_NAME" "BTC watch 타이머"

    if [[ "$CHECK_ONLY" -eq 1 ]]; then
        return
    fi

    capture_restart_cursor
    CURRENT_STEP="$SERVICE_NAME 재시작"
    sudo systemctl restart "$SERVICE_NAME"
}

verify_main_process() {
    local parser_output

    CURRENT_STEP="서비스 active 상태 확인"
    verify_unit_active "$SERVICE_NAME" "BitSwipe 서비스"

    CURRENT_STEP="서비스 MainPID 확인"
    MAIN_PID="$(systemctl show "$SERVICE_NAME" --property=MainPID --value)" || \
        fail "$SERVICE_NAME의 MainPID를 읽을 수 없습니다."
    MAIN_PID="${MAIN_PID//[[:space:]]/}"
    [[ "$MAIN_PID" =~ ^[1-9][0-9]*$ ]] || \
        fail "$SERVICE_NAME의 MainPID가 0이거나 올바르지 않습니다: ${MAIN_PID:-비어 있음}"

    CURRENT_STEP="실행 중 프로세스 기능 플래그 확인"
    if ! parser_output="$(sudo python3 -I -S -X utf8 "$ENV_PARSER_PATH" process-environment \
        "/proc/${MAIN_PID}/environ" 2>&1)"; then
        fail "MainPID $MAIN_PID의 실행 환경을 안전하게 검사할 수 없습니다. ${parser_output:-자세한 오류 없음}"
    fi
}

verify_http_endpoints() {
    local http_code

    CURRENT_STEP="웹 루트 HTTP 200 확인"
    if ! http_code="$(curl --silent --show-error --retry 5 --retry-connrefused \
        --retry-delay 1 --connect-timeout 2 --max-time 5 --output /dev/null \
        --write-out '%{http_code}' 'http://127.0.0.1:8000/')"; then
        fail "BitSwipe 웹 루트에 연결할 수 없습니다."
    fi
    [[ "$http_code" == "200" ]] || \
        fail "BitSwipe 웹 루트가 HTTP 200이 아닙니다. 실제: $http_code"

    CURRENT_STEP="/api/connections JSON 확인"
    if ! curl --fail --silent --show-error --retry 5 --retry-connrefused \
        --retry-delay 1 --connect-timeout 2 --max-time 5 \
        'http://127.0.0.1:8000/api/connections' | \
        python3 -c 'import json, sys; json.load(sys.stdin)' >/dev/null; then
        fail "/api/connections 응답이 성공한 HTTP 응답과 올바른 JSON 형식을 모두 만족하지 않습니다."
    fi
}

verify_recent_logs() {
    local recent_logs

    CURRENT_STEP="최근 서비스 로그 확인"
    if [[ "$CHECK_ONLY" -eq 1 ]]; then
        recent_logs="$(sudo journalctl --unit="$SERVICE_NAME" --since='10 minutes ago' \
            --no-pager --output=cat)" || fail "최근 $SERVICE_NAME 로그를 읽을 수 없습니다."
    else
        recent_logs="$(sudo journalctl --unit="$SERVICE_NAME" --after-cursor="$RESTART_CURSOR" \
            --no-pager --output=cat)" || fail "재시작 이후 $SERVICE_NAME 로그를 읽을 수 없습니다."
    fi

    if [[ "$recent_logs" =~ $FATAL_LOG_PATTERN ]]; then
        printf '\n치명적 시작 오류로 판단한 로그:\n' >&2
        printf '%s\n' "$recent_logs" | grep -E -- "$FATAL_LOG_PATTERN" >&2 || true
        fail "최근 서비스 로그에 치명적 시작 오류가 있습니다."
    fi
}

verify_database_read_only() {
    CURRENT_STEP="Scenario Ledger 데이터베이스 읽기 전용 검증"
    [[ -f "$DATABASE_PATH" ]] || fail "$DATABASE_PATH 파일이 없습니다. 데이터베이스를 새로 만들지는 않았습니다."

    if ! python3 - "$DATABASE_PATH" <<'PY'
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

db_path = Path(sys.argv[1]).resolve(strict=True)
uri = f"file:{quote(db_path.as_posix(), safe='/:')}?mode=ro"
connection = sqlite3.connect(uri, uri=True)
try:
    connection.execute("PRAGMA query_only = ON")
    integrity_rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if integrity_rows != ["ok"]:
        raise SystemExit(f"integrity_check 결과가 ok가 아닙니다: {integrity_rows!r}")
    scenario_count = connection.execute("SELECT COUNT(*) FROM scenarios").fetchone()[0]
    event_count = connection.execute("SELECT COUNT(*) FROM scenario_events").fetchone()[0]
finally:
    connection.close()

print("데이터베이스 무결성: ok")
print(f"시나리오 수: {scenario_count}")
print(f"이벤트 수: {event_count}")
PY
    then
        fail "Scenario Ledger 데이터베이스 읽기 전용 검증에 실패했습니다. 데이터베이스는 초기화하거나 수정하지 않았습니다."
    fi
}

main() {
    trap 'print_exit_summary "$?"' EXIT
    parse_args "$@"

    CURRENT_STEP="$APP_DIR 저장소 진입"
    enter_repository
    verify_and_update_git
    verify_systemd_flag_disabled
    restart_service_if_requested
    verify_main_process
    verify_http_endpoints

    CURRENT_STEP="BTC watch timer 최종 상태 확인"
    verify_unit_active "$WATCH_TIMER_NAME" "BTC watch 타이머"

    verify_recent_logs
    verify_database_read_only

    CURRENT_STEP="완료"
    SUCCESS=1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
