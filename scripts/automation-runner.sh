#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
automation_dir="${repo_root}/cache/automation"

usage() {
  cat <<'EOF'
Usage: scripts/automation-runner.sh <db|npm-full-scan>

Run one scheduled av.db maintenance job with repo-local logging,
environment loading, locking, timeout handling, and status recording.
EOF
}

load_environment() {
  export PATH="/usr/local/bin:/opt/homebrew/bin:${repo_root}/scripts/bin:${PATH}"
  export AWS_PAGER="${AWS_PAGER:-}"

  for env_file in "${repo_root}/.env" "${repo_root}/../automic-vault/.env"; do
    if [[ -f "${env_file}" ]]; then
      set -a
      # shellcheck disable=SC1090
      source "${env_file}"
      set +a
    fi
  done

  export CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-Developer ID Application: Max Howell (ZU76A67LGU)}"
}

write_status() {
  local job="$1"
  local state="$2"
  local exit_code="$3"
  local started_at="$4"
  local ended_at="$5"
  local log_path="$6"
  local status_path="${automation_dir}/${job}.status.json"

  python3 - "$status_path" "$job" "$state" "$exit_code" "$started_at" "$ended_at" "$log_path" <<'PY'
import json
import pathlib
import sys

path, job, state, exit_code, started_at, ended_at, log_path = sys.argv[1:]
payload = {
    "job": job,
    "state": state,
    "exit_code": int(exit_code),
    "started_at": started_at,
    "ended_at": ended_at,
    "log_path": log_path,
}
pathlib.Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
PY
}

run_with_timeout() {
  local timeout_seconds="$1"
  shift

  python3 - "$timeout_seconds" "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout = int(sys.argv[1])
command = sys.argv[2:]
process = subprocess.Popen(command, start_new_session=True)
try:
    raise SystemExit(process.wait(timeout=timeout))
except KeyboardInterrupt:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        raise SystemExit(process.wait())
    try:
        process.wait(timeout=30)
        raise SystemExit(130)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise SystemExit(130)
except subprocess.TimeoutExpired:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        raise SystemExit(process.wait())
    try:
        process.wait(timeout=30)
        raise SystemExit(124)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        raise SystemExit(124)
PY
}

run_job_unlocked() {
  local job="$1"
  local log_path="${automation_dir}/${job}.log"
  local started_at ended_at exit_code timeout_seconds
  local status_recorded=0

  finalize_status() {
    local final_exit_code="${1:-0}"
    local current_status_recorded="${status_recorded:-0}"

    if [[ "${current_status_recorded}" -eq 1 ]]; then
      return
    fi

    ended_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    if [[ "${final_exit_code}" -eq 0 ]]; then
      printf '[%s] Finished %s automation\n' "${ended_at}" "${job}"
      write_status "${job}" "ok" "${final_exit_code}" "${started_at}" "${ended_at}" "${log_path}"
    elif [[ "${final_exit_code}" -eq 124 ]]; then
      printf '[%s] Timed out %s automation\n' "${ended_at}" "${job}"
      write_status "${job}" "timeout" "${final_exit_code}" "${started_at}" "${ended_at}" "${log_path}"
    else
      printf '[%s] Failed %s automation with exit code %s\n' "${ended_at}" "${job}" "${final_exit_code}"
      write_status "${job}" "failed" "${final_exit_code}" "${started_at}" "${ended_at}" "${log_path}"
    fi
    status_recorded=1
  }

  mkdir -p "${automation_dir}"
  started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  write_status "${job}" "running" 0 "${started_at}" "" "${log_path}"

  exec >>"${log_path}" 2>&1
  printf '\n[%s] Starting %s automation\n' "${started_at}" "${job}"
  cd "${repo_root}"
  load_environment

  trap 'finalize_status "$?"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  set +e
  case "${job}" in
    db)
      timeout_seconds="${AVDB_AUTOMATION_DB_TIMEOUT_SECONDS:-21600}"
      run_with_timeout "${timeout_seconds}" "${script_dir}/hourly-maintenance.py"
      exit_code=$?
      ;;
    npm-full-scan)
      timeout_seconds="${AVDB_AUTOMATION_NPM_FULL_SCAN_TIMEOUT_SECONDS:-43200}"
      run_with_timeout "${timeout_seconds}" \
        bash -euo pipefail -c \
        '"${0}/build-db.py" --refresh --npm-full-scan && "${0}/build-combined-json.py"' \
        "${script_dir}"
      exit_code=$?
      ;;
    *)
      usage
      exit_code=64
      ;;
  esac
  set -e

  finalize_status "${exit_code}"
  trap - EXIT INT TERM

  return "${exit_code}"
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--run-unlocked" ]]; then
  [[ $# -eq 2 ]] || {
    usage >&2
    exit 64
  }
  run_job_unlocked "$2"
  exit $?
fi

[[ $# -eq 1 ]] || {
  usage >&2
  exit 64
}

mkdir -p "${automation_dir}"
exec lockf -t 0 "${automation_dir}/$1.lock" "$0" --run-unlocked "$1"
