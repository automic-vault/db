#!/usr/bin/env bash

set -euo pipefail

org="automic-vault"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
clone_root="${AUTOMIC_VAULT_REPO_CACHE:-${repo_root}/../isotopes}"
radioisotopes_repo="${org}/radioisotopes"
radioisotopes_dir="${AUTOMIC_VAULT_RADIOISOTOPES_REPO:-${repo_root}/../radioisotopes}"
dry_run=false
skip_builds=false
isotope_versions_path="${AV_ISOTOPES_JSON_PATH:-${repo_root}/cache/automic-vault/isotopes.json}"
curl_connect_timeout="${AV_ISOTOPES_CURL_CONNECT_TIMEOUT_SECONDS:-15}"
curl_max_time="${AV_ISOTOPES_CURL_MAX_TIME_SECONDS:-120}"
curl_retry_count="${AV_ISOTOPES_CURL_RETRY_COUNT:-3}"
homebrew_formula_index_cache=""
homebrew_formula_index_entry_result=""
homebrew_formula_index_cache_path="${AV_HOMEBREW_FORMULA_INDEX_CACHE_PATH:-${repo_root}/cache/automic-vault/homebrew-formula-index.json}"
RUN_PUBLISH_COMMAND_AUTH_UNAVAILABLE=0
if [[ -n "${AUTOMIC_VAULT_CODEX_PROJECT_ROOT:-}" ]]; then
  codex_project_root="${AUTOMIC_VAULT_CODEX_PROJECT_ROOT}"
elif [[ -n "${HOME:-}" ]]; then
  codex_project_root="${HOME}/src/automic-vault"
else
  codex_project_root="${repo_root}"
fi
codex_conflict_max_attempts="${AUTOMIC_VAULT_CODEX_CONFLICT_MAX_ATTEMPTS:-3}"

usage() {
  cat <<'EOF'
Usage: scripts/build-isotopes.sh [--clone-root PATH] [--dry-run]
                                 [--repo NAME] [--skip-builds]

Clone automic-vault/radioisotopes and all automic-vault GitHub repositories
that are forks. Set each fork's upstream remote to the original repository,
detect new upstream GitHub releases, rebuild changed repos with the build
instructions in `automic-vault.yml`, and publish the resulting isotope archive
to a new release on the automic-vault fork.

Options:
  --clone-root PATH  Directory used for local fork clones.
                     Defaults to ../isotopes.
  --dry-run          Print actions without pulling, building, or releasing.
  --repo NAME        Only process one automic-vault repository.
  --skip-builds      Skip build and release work. Still refresh isotopes.json.
  --help            Show this help.

Environment:
  AUTOMIC_VAULT_CODEX_PROJECT_ROOT
                    Project root passed to Codex when repairing isotope merge
                    conflicts. Defaults to ~/src/automic-vault.
  AUTOMIC_VAULT_CODEX_CONFLICT_MAX_ATTEMPTS
                    Number of Codex repair attempts per conflicted git update.
                    Defaults to 3.
EOF
}

only_repo=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --clone-root)
      clone_root="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --repo)
      only_repo="$2"
      shift 2
      ;;
    --skip-builds)
      skip_builds=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

for tool in curl gh git jq ruby; do
  command -v "${tool}" >/dev/null 2>&1 || {
    echo "Missing required tool: ${tool}" >&2
    exit 1
  }
done

mkdir -p "${clone_root}" "$(dirname "${radioisotopes_dir}")"

curl_fetch() {
  curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --connect-timeout "${curl_connect_timeout}" \
    --max-time "${curl_max_time}" \
    --retry "${curl_retry_count}" \
    --retry-delay 2 \
    --retry-all-errors \
    "$@"
}

curl_fetch_no_retry() {
  curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --connect-timeout "${curl_connect_timeout}" \
    --max-time "${curl_max_time}" \
    "$@"
}

ensure_homebrew_formula_index() {
  if [[ -z "${homebrew_formula_index_cache}" ]]; then
    homebrew_formula_index_cache="${homebrew_formula_index_cache_path}"
  fi

  if [[ -s "${homebrew_formula_index_cache}" ]]; then
    return 0
  fi

  mkdir -p "$(dirname "${homebrew_formula_index_cache}")"

  local tmp_cache
  tmp_cache="$(mktemp "${homebrew_formula_index_cache}.tmp.XXXXXX")"
  if ! curl_fetch "https://formulae.brew.sh/api/formula.json" >"${tmp_cache}"; then
    rm -f "${tmp_cache}"
    return 1
  fi

  mv -f "${tmp_cache}" "${homebrew_formula_index_cache}"
}

homebrew_formula_index_entry() {
  local formula="$1"

  ensure_homebrew_formula_index
  homebrew_formula_index_entry_result="$(
    jq -c --arg formula "${formula}" '
    .[]
    | select(.name == $formula or ((.aliases // []) | index($formula)))
  ' "${homebrew_formula_index_cache}" |
    head -n 1
  )"
}

ensure_radioisotopes_clone() {
  if [[ -d "${radioisotopes_dir}/.git" ]]; then
    return 0
  fi

  if [[ -e "${radioisotopes_dir}" ]]; then
    echo "Clone path exists but is not a git repo: ${radioisotopes_dir}" >&2
    return 1
  fi

  if [[ "${dry_run}" == "true" ]]; then
    echo "Would clone ${radioisotopes_repo} to ${radioisotopes_dir}"
    return 0
  fi

  echo "Cloning ${radioisotopes_repo}"
  gh repo clone "${radioisotopes_repo}" "${radioisotopes_dir}"
}

update_radioisotopes_clone() {
  local default_branch

  ensure_radioisotopes_clone

  default_branch="$(
    gh repo view "${radioisotopes_repo}" \
      --json defaultBranchRef \
      --jq '.defaultBranchRef.name'
  )"

  if [[ "${dry_run}" == "true" ]]; then
    echo "Would fetch origin ${default_branch} without tags in ${radioisotopes_dir}"
    echo "Would rebase onto origin ${default_branch} in ${radioisotopes_dir}"
    return 0
  fi

  fetch_branch_without_tags "${radioisotopes_dir}" origin "${default_branch}"
  rebase_onto_remote_branch "${radioisotopes_dir}" origin "${default_branch}"
}

sanitize_version() {
  local version="$1"

  version="${version#refs/tags/}"
  version="${version#v}"
  version="${version//\//-}"
  version="${version// /-}"
  printf '%s\n' "${version}"
}

ensure_clone() {
  local repo_name="$1"
  local repo_dir="$2"

  if [[ -d "${repo_dir}/.git" ]]; then
    return 0
  fi

  if [[ -e "${repo_dir}" ]]; then
    echo "Clone path exists but is not a git repo: ${repo_dir}" >&2
    return 1
  fi

  echo "Cloning ${org}/${repo_name}"
  gh repo clone "${org}/${repo_name}" "${repo_dir}"
}

set_upstream_remote() {
  local repo_dir="$1"
  local upstream_repo="$2"
  local upstream_url="https://github.com/${upstream_repo}.git"

  if git -C "${repo_dir}" remote get-url upstream >/dev/null 2>&1; then
    git -C "${repo_dir}" remote set-url upstream "${upstream_url}"
  else
    git -C "${repo_dir}" remote add upstream "${upstream_url}"
  fi
}

fetch_branch_without_tags() {
  local repo_dir="$1"
  local remote="$2"
  local branch="$3"

  git -C "${repo_dir}" fetch --no-tags "${remote}" \
    "refs/heads/${branch}:refs/remotes/${remote}/${branch}"
}

rebase_onto_remote_branch() {
  local repo_dir="$1"
  local remote="$2"
  local branch="$3"

  rebase_with_codex_conflict_repair \
    "${repo_dir}" \
    "refs/remotes/${remote}/${branch}" \
    "git rebase refs/remotes/${remote}/${branch}"
}

rebase_onto_ref() {
  local repo_dir="$1"
  local ref="$2"

  rebase_with_codex_conflict_repair \
    "${repo_dir}" \
    "${ref}" \
    "git rebase ${ref}"
}

git_has_unmerged_paths() {
  local repo_dir="$1"

  git -C "${repo_dir}" diff --name-only --diff-filter=U | grep -q .
}

git_rebase_in_progress() {
  local repo_dir="$1"
  local rebase_apply rebase_merge

  rebase_apply="$(
    git -C "${repo_dir}" rev-parse --path-format=absolute --git-path rebase-apply 2>/dev/null
  )" || return 1
  rebase_merge="$(
    git -C "${repo_dir}" rev-parse --path-format=absolute --git-path rebase-merge 2>/dev/null
  )" || return 1

  [[ -d "${rebase_apply}" || -d "${rebase_merge}" ]]
}

git_operation_in_progress() {
  local repo_dir="$1"

  git_rebase_in_progress "${repo_dir}" ||
    git -C "${repo_dir}" rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1 ||
    git -C "${repo_dir}" rev-parse -q --verify CHERRY_PICK_HEAD >/dev/null 2>&1 ||
    git -C "${repo_dir}" rev-parse -q --verify REVERT_HEAD >/dev/null 2>&1
}

git_conflict_state_exists() {
  local repo_dir="$1"

  git_has_unmerged_paths "${repo_dir}" || git_operation_in_progress "${repo_dir}"
}

git_tracked_changes_are_clean() {
  local repo_dir="$1"

  git -C "${repo_dir}" diff --quiet &&
    git -C "${repo_dir}" diff --cached --quiet
}

continue_rebase_if_ready() {
  local repo_dir="$1"

  while git_rebase_in_progress "${repo_dir}"; do
    if git_has_unmerged_paths "${repo_dir}"; then
      return 1
    fi
    GIT_EDITOR=: git -C "${repo_dir}" rebase --continue || return 1
  done
}

git_update_is_complete() {
  local repo_dir="$1"

  ! git_has_unmerged_paths "${repo_dir}" &&
    ! git_operation_in_progress "${repo_dir}" &&
    git_tracked_changes_are_clean "${repo_dir}"
}

normalized_codex_conflict_attempts() {
  case "${codex_conflict_max_attempts}" in
    ''|*[!0-9]*)
      printf '3\n'
      ;;
    0)
      printf '1\n'
      ;;
    *)
      printf '%s\n' "${codex_conflict_max_attempts}"
      ;;
  esac
}

invoke_codex_for_git_conflicts() {
  local repo_dir="$1"
  local operation="$2"
  local target_ref="$3"
  local prompt

  if ! command -v codex >/dev/null 2>&1; then
    echo "Codex is required to repair isotope merge conflicts but was not found on PATH" >&2
    return 127
  fi

  if [[ ! -d "${codex_project_root}" ]]; then
    echo "Codex project root does not exist: ${codex_project_root}" >&2
    return 1
  fi

  prompt="$(cat <<EOF
Resolve the interrupted Automic Vault isotope git update.

Primary project root: ${codex_project_root}
Conflicted isotope checkout: ${repo_dir}
Failed operation: ${operation}
Target ref: ${target_ref}

This was invoked by scripts/update-all through scripts/build-isotopes.sh.
Use the Automic Vault project context to understand the fork, then work inside
the conflicted isotope checkout. Resolve the merge/rebase conflicts, preserve
both upstream changes and Automic Vault fork behavior when appropriate, and
complete the interrupted git operation until there are no unmerged paths and no
rebase, merge, cherry-pick, or revert operation in progress.

Run focused checks if practical. Leave unrelated files alone. Do not abort or
skip the git operation unless it is genuinely impossible to continue.
EOF
)"

  codex exec \
    --cd "${codex_project_root}" \
    --add-dir "${repo_dir}" \
    --sandbox workspace-write \
    --config 'approval_policy="never"' \
    --color never \
    --ephemeral \
    "${prompt}" \
    >&2
}

repair_git_conflicts_with_codex() {
  local repo_dir="$1"
  local operation="$2"
  local target_ref="$3"
  local original_status="$4"
  local attempt max_attempts

  if ! git_conflict_state_exists "${repo_dir}"; then
    return "${original_status}"
  fi

  max_attempts="$(normalized_codex_conflict_attempts)"
  attempt=1
  while [[ "${attempt}" -le "${max_attempts}" ]]; do
    echo "Git update conflict in ${repo_dir}; invoking Codex to repair it (attempt ${attempt}/${max_attempts})" >&2
    if ! invoke_codex_for_git_conflicts "${repo_dir}" "${operation}" "${target_ref}"; then
      return 1
    fi

    continue_rebase_if_ready "${repo_dir}" || true

    if git_update_is_complete "${repo_dir}"; then
      return 0
    fi

    attempt=$((attempt + 1))
  done

  echo "Codex did not finish repairing git conflicts in ${repo_dir}" >&2
  git -C "${repo_dir}" status --short >&2 || true
  return 1
}

rebase_with_codex_conflict_repair() {
  local repo_dir="$1"
  local target_ref="$2"
  local operation="$3"
  local status

  if git -C "${repo_dir}" rebase "${target_ref}"; then
    return 0
  fi

  status="$?"
  repair_git_conflicts_with_codex "${repo_dir}" "${operation}" "${target_ref}" "${status}"
}

fetch_release_tag_if_missing() {
  local repo_dir="$1"
  local remote="$2"
  local tag="$3"

  if git -C "${repo_dir}" rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
    return 0
  fi

  git -C "${repo_dir}" fetch --no-tags "${remote}" \
    "refs/tags/${tag}:refs/tags/${tag}"
}

move_release_tag_to_head() {
  local repo_dir="$1"
  local tag="$2"

  git -C "${repo_dir}" tag -f "${tag}" HEAD
}

release_exists_on_fork() {
  local fork_repo="$1"
  local tag="$2"

  gh release view "${tag}" --repo "${fork_repo}" >/dev/null 2>&1
}

latest_release_json() {
  local upstream_repo="$1"

  gh api \
    -H "Accept: application/vnd.github+json" \
    "/repos/${upstream_repo}/releases/latest" 2>/dev/null || true
}

latest_fork_release_json() {
  local fork_repo="$1"

  gh api \
    -H "Accept: application/vnd.github+json" \
    "/repos/${fork_repo}/releases/latest" 2>/dev/null || true
}

homebrew_formula_release_json() {
  local formula="$1"
  local formula_json canonical_formula version

  if [[ "${formula}" == */*/* ]]; then
    homebrew_tap_formula_release_json "${formula}"
    return
  fi

  if ! homebrew_formula_index_entry "${formula}"; then
    echo "Failed to load Homebrew formula index while resolving ${formula}" >&2
    return 1
  fi
  formula_json="${homebrew_formula_index_entry_result}"
  if [[ -z "${formula_json}" ]]; then
    echo "Homebrew formula ${formula} was not found in the current formulae.brew.sh index" >&2
    return 1
  fi
  canonical_formula="$(printf '%s\n' "${formula_json}" | jq -r '.name')"
  version="$(printf '%s\n' "${formula_json}" | jq -r '.versions.stable')"

  if [[ -z "${version}" || "${version}" == "null" ]]; then
    echo "Homebrew formula ${canonical_formula} did not include a stable version" >&2
    return 1
  fi

  jq -n \
    --arg tag "v${version}" \
    --arg htmlUrl "https://formulae.brew.sh/formula/${canonical_formula}" \
    --arg publishedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{
      tag_name: $tag,
      html_url: $htmlUrl,
      published_at: $publishedAt,
      assets: []
    }'
}

homebrew_versioned_formulae() {
  local formula="$1"

  ensure_homebrew_formula_index
  jq -r --arg base "${formula}" '
    .[]
    | .name?
    | select(type == "string")
    | select(startswith($base + "@"))
    | select(.[(($base | length) + 1):] | test("^[0-9]+$"))
  ' "${homebrew_formula_index_cache}" |
    sort -u
}

homebrew_tap_formula_release_json() {
  local formula="$1"
  local owner tap name repo formula_rb formula_path version html_url

  IFS='/' read -r owner tap name <<< "${formula}"
  if [[ -z "${owner}" || -z "${tap}" || -z "${name}" ]]; then
    echo "Unsupported Homebrew tap formula name ${formula}" >&2
    return 1
  fi

  repo="${owner}/homebrew-${tap}"
  for formula_path in "Formula/${name}.rb" "${name}.rb"; do
    if formula_rb="$(curl_fetch_no_retry "https://raw.githubusercontent.com/${repo}/HEAD/${formula_path}" 2>/dev/null)"; then
      break
    fi
  done
  if [[ -z "${formula_rb}" ]]; then
    echo "Failed to fetch Homebrew tap formula ${formula}" >&2
    return 1
  fi

  version="$(printf '%s\n' "${formula_rb}" |
    ruby -e '
      content = STDIN.read
      vars = {}
      content.each_line do |line|
        if line =~ /^\s*([A-Za-z_]\w*)\s*=\s*"([^"]+)"/
          vars[$1] = $2
        end
      end
      content.each_line do |line|
        if line =~ /^\s*version\s+"([^"]+)"/
          puts $1
          exit
        end
        if line =~ /^\s*version\s+([A-Za-z_]\w*)/ && vars[$1]
          puts vars[$1]
          exit
        end
      end
    ' |
    head -n 1)"
  if [[ -z "${version}" ]]; then
    version="$(printf '%s\n' "${formula_rb}" |
      sed -nE 's#^[[:space:]]*url[[:space:]]+"https://releases\.hashicorp\.com/'"${name}"'/([^/]+)/.*#\1#p' |
      head -n 1)"
  fi
  if [[ -z "${version}" ]]; then
    echo "Homebrew tap formula ${formula} did not include a stable version" >&2
    return 1
  fi

  html_url="https://github.com/${repo}/blob/HEAD/${formula_path}"
  jq -n \
    --arg tag "v${version}" \
    --arg htmlUrl "${html_url}" \
    --arg publishedAt "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{
      tag_name: $tag,
      html_url: $htmlUrl,
      published_at: $publishedAt,
      assets: []
    }'
}

homebrew_formula_repository() {
  local formula="$1"
  local owner tap name

  if [[ "${formula}" == */*/* ]]; then
    IFS='/' read -r owner tap name <<< "${formula}"
    if [[ -n "${owner}" && -n "${tap}" && -n "${name}" ]]; then
      printf '%s\n' "${owner}/homebrew-${tap}"
      return 0
    fi
  fi

  printf '%s\n' "Homebrew/homebrew-core"
}

manifest_json() {
  local manifest_path="$1"

  if [[ ! -f "${manifest_path}" ]]; then
    echo "Missing manifest: ${manifest_path}" >&2
    return 1
  fi

  env RUBYOPT=-W0 ruby -ryaml -rjson -e '
    path = ARGV.fetch(0)
    data = YAML.safe_load(
      File.read(path),
      permitted_classes: [],
      permitted_symbols: [],
      aliases: false
    ) || {}
    puts JSON.generate(data)
  ' "${manifest_path}"
}

manifest_field() {
  local manifest_path="$1"
  local field_name="$2"
  local manifest

  manifest="$(manifest_json "${manifest_path}")"
  printf '%s\n' "${manifest}" | jq -r --arg field "${field_name}" '
    .[$field] // empty
  '
}

manifest_homebrew_formula() {
  local repo_name="$1"
  local manifest_path="$2"
  local manifest formula

  manifest="$(manifest_json "${manifest_path}")"
  formula="$(printf '%s\n' "${manifest}" | jq -r '
    .homebrewFormula // .homebrew_formula // .source.homebrewFormula //
      .source.homebrew_formula // empty
  ')"

  if [[ -n "${formula}" ]]; then
    printf '%s\n' "${formula}"
    return 0
  fi

  case "${repo_name}" in
    aws-cli)
      printf 'awscli\n'
      ;;
  esac
}

repo_source_json() {
  local repo_name="$1"
  local repo_json="$2"
  local repo_dir="$3"
  local manifest_path="${repo_dir}/automic-vault.yml"
  local parent upstream_repo upstream_default formula release_json

  parent="$(printf '%s\n' "${repo_json}" | jq -r '.parent')"
  if [[ "${parent}" != "null" ]]; then
    upstream_repo="$(printf '%s\n' "${repo_json}" | jq -r '.parent.full_name')"
    upstream_default="$(printf '%s\n' "${repo_json}" | jq -r '.parent.default_branch')"
    release_json="$(latest_release_json "${upstream_repo}")"
    jq -n \
      --arg kind "github" \
      --arg upstreamRepo "${upstream_repo}" \
      --arg upstreamDefault "${upstream_default}" \
      --arg upstreamName "${upstream_repo##*/}" \
      --argjson release "${release_json:-null}" \
      '{
        kind: $kind,
        upstreamRepo: $upstreamRepo,
        upstreamDefault: $upstreamDefault,
        upstreamName: $upstreamName,
        release: $release
      }'
    return 0
  fi

  formula="$(manifest_homebrew_formula "${repo_name}" "${manifest_path}")"
  if [[ -n "${formula}" ]]; then
    release_json="$(homebrew_formula_release_json "${formula}")"
    jq -n \
      --arg kind "homebrew" \
      --arg upstreamRepo "$(homebrew_formula_repository "${formula}")" \
      --arg upstreamDefault "$(printf '%s\n' "${repo_json}" | jq -r '.default_branch')" \
      --arg upstreamName "${formula}" \
      --arg formula "${formula}" \
      --argjson release "${release_json}" \
      '{
        kind: $kind,
        upstreamRepo: $upstreamRepo,
        upstreamDefault: $upstreamDefault,
        upstreamName: $upstreamName,
        formula: $formula,
        release: $release
      }'
    return 0
  fi

  jq -n --arg reason "repository is not a fork" \
    '{kind: "unsupported", reason: $reason}'
}

find_isotope_output() {
  local repo_dir="$1"
  local repo_name="$2"
  local nested_output="${repo_dir}/isotopes/${repo_name}/out.tgz"
  local root_output="${repo_dir}/out.tgz"

  if [[ -f "${nested_output}" ]]; then
    printf '%s\n' "${nested_output}"
    return 0
  fi

  if [[ -f "${root_output}" ]]; then
    printf '%s\n' "${root_output}"
    return 0
  fi

  return 1
}

publish_release() {
  local fork_repo="$1"
  local tag="$2"
  local upstream_repo="$3"
  local upstream_release_url="$4"
  local archive_path="$5"

  if [[ "${dry_run}" == "true" ]]; then
    echo "Would create release ${tag} on ${fork_repo} with ${archive_path}"
    return 0
  fi

  gh release create "${tag}" "${archive_path}" \
    --repo "${fork_repo}" \
    --title "${tag}" \
    --verify-tag \
    --notes "Built from ${upstream_repo} ${tag}: ${upstream_release_url}"
}

publish_auth_failure() {
  local output="$1"

  case "${output}" in
    *"Denied by operator"*|*"Authentication failed"*|*"could not read Username for 'https://github.com'"*|*"could not read Password for 'https://github.com'"*|*"unable to get password from user"*)
      return 0
      ;;
  esac

  return 1
}

run_publish_command() {
  local output status
  local -a command

  RUN_PUBLISH_COMMAND_AUTH_UNAVAILABLE=0

  if [[ "${1:-}" == "git" ]]; then
    command=(
      git
      -c credential.helper=
      -c credential.interactive=never
      -c core.askPass=
      "${@:2}"
    )
  else
    command=("$@")
  fi

  set +e
  output="$(
    GIT_TERMINAL_PROMPT=0 \
    GCM_INTERACTIVE=never \
    GIT_ASKPASS=/usr/bin/false \
    SSH_ASKPASS=/usr/bin/false \
    "${command[@]}" 2>&1
  )"
  status=$?
  set -e

  if [[ -n "${output}" ]]; then
    printf '%s\n' "${output}"
  fi

  if [[ "${status}" -eq 0 ]]; then
    return 0
  fi

  if publish_auth_failure "${output}"; then
    RUN_PUBLISH_COMMAND_AUTH_UNAVAILABLE=1
    return 0
  fi

  return "${status}"
}

append_isotope_version_entry() {
  local entries_path="$1"
  local repo_name="$2"
  local fork_repo="$3"
  local upstream_repo="$4"
  local release_json="$5"
  local isotope_name="$6"
  local replaces="$7"
  local modifies="$8"
  local migrate_script="$9"
  local justification_json="${10}"
  local caveats_json="${11}"
  local applies_to_versioned="${12}"
  local tag version

  tag="$(printf '%s\n' "${release_json}" | jq -r '.tag_name')"
  if [[ -z "${tag}" || "${tag}" == "null" ]]; then
    return 0
  fi

  version="$(sanitize_version "${tag}")"

  printf '%s\n' "${release_json}" | jq -c \
    --arg repoName "${repo_name}" \
    --arg name "${isotope_name}" \
    --arg repository "${fork_repo}" \
    --arg upstreamRepository "${upstream_repo}" \
    --arg version "${version}" \
    --arg replaces "${replaces}" \
    --arg modifies "${modifies}" \
    --arg migrate "${migrate_script}" \
    --argjson justification "${justification_json}" \
    --argjson caveats "${caveats_json}" \
    --argjson appliesToVersioned "${applies_to_versioned}" \
    '
      ([.assets[]? | select(.name | endswith(".tgz"))][0]) as $asset
      | {
          repoName: $repoName,
          name: $name,
          replaces: ($replaces | if . == "" then null else . end),
          modifies: ($modifies | if . == "" then null else . end),
          migrate: ($migrate | if . == "" then null else . end),
          repository: $repository,
          upstreamRepository: $upstreamRepository,
          version: $version,
          tag: .tag_name,
          releaseUrl: .html_url,
          archiveName: ($asset.name // null),
          archiveUrl: ($asset.browser_download_url // null),
          publishedAt: .published_at
        }
      | if $justification == null then . else . + {justification: $justification} end
      | if $caveats == null then . else . + {caveats: $caveats} end
      | if $appliesToVersioned then . + {appliesToVersionedFormulae: true} else . end
    ' >>"${entries_path}"
}

generate_isotope_versions_json() {
  local repo_names="$1"
  local entries_path tmp_path
  local repo_name fork_repo repo_json source_json source_kind upstream_repo release_json
  local repo_dir manifest_path manifest isotope_name replaces migrate_script
  local justification_json caveats_json

  mkdir -p "$(dirname "${isotope_versions_path}")"
  entries_path="$(mktemp)"
  tmp_path="$(mktemp "${isotope_versions_path}.tmp.XXXXXX")"

  while IFS= read -r repo_name; do
    [[ -n "${repo_name}" ]] || continue

    fork_repo="${org}/${repo_name}"
    repo_json="$(gh api "/repos/${fork_repo}")"

    repo_dir="${clone_root}/${repo_name}"
    ensure_clone "${repo_name}" "${repo_dir}"
    source_json="$(repo_source_json "${repo_name}" "${repo_json}" "${repo_dir}")"
    source_kind="$(printf '%s\n' "${source_json}" | jq -r '.kind')"
    if [[ "${source_kind}" == "unsupported" ]]; then
      continue
    fi

    upstream_repo="$(printf '%s\n' "${source_json}" | jq -r '.upstreamRepo')"
    if [[ -z "${upstream_repo}" || "${upstream_repo}" == "null" ]]; then
      continue
    fi

    release_json="$(latest_fork_release_json "${fork_repo}")"
    if [[ -z "${release_json}" ]]; then
      continue
    fi

    manifest_path="${repo_dir}/automic-vault.yml"
    manifest="$(manifest_json "${manifest_path}")"
    isotope_name="$(printf '%s\n' "${manifest}" | jq -r '.name // empty')"
    replaces="$(printf '%s\n' "${manifest}" | jq -r '.replaces // empty')"
    migrate_script="$(printf '%s\n' "${manifest}" | jq -r '.migrate // empty')"
    justification_json="$(printf '%s\n' "${manifest}" | jq -c '.justification // null')"
    caveats_json="$(printf '%s\n' "${manifest}" | jq -c '.caveats // null')"

    if [[ -z "${isotope_name}" ]]; then
      echo "Missing required manifest field 'name' in ${manifest_path}" >&2
      rm -f "${entries_path}" "${tmp_path}"
      return 1
    fi

    append_isotope_version_entry \
      "${entries_path}" \
      "${repo_name}" \
      "${fork_repo}" \
      "${upstream_repo}" \
      "${release_json}" \
      "${isotope_name}" \
      "${replaces}" \
      "" \
      "${migrate_script}" \
      "${justification_json}" \
      "${caveats_json}" \
      "false"
  done <<<"${repo_names}"

  append_radioisotope_version_entries "${entries_path}"

  jq -s '
    sort_by(.repoName)
    | map({(.repoName): (del(.repoName))})
    | add // {}
  ' "${entries_path}" >"${tmp_path}"

  mv -f "${tmp_path}" "${isotope_versions_path}"
  rm -f "${entries_path}"
  echo "Wrote ${isotope_versions_path}"
}

append_radioisotope_version_entries() {
  local entries_path="$1"
  local radio_dir manifest_path manifest isotope_name modifies formula release_json
  local justification_json caveats_json repo_name
  local applies_to_versioned versioned_formula versioned_release_json

  if [[ ! -d "${radioisotopes_dir}" ]]; then
    return 0
  fi

  for radio_dir in "${radioisotopes_dir}"/*; do
    [[ -d "${radio_dir}" ]] || continue
    manifest_path="${radio_dir}/automic-vault.yml"
    [[ -f "${manifest_path}" ]] || continue

    manifest="$(manifest_json "${manifest_path}")"
    isotope_name="$(printf '%s\n' "${manifest}" | jq -r '.name // empty')"
    modifies="$(printf '%s\n' "${manifest}" | jq -r '.modifies // empty')"
    justification_json="$(printf '%s\n' "${manifest}" | jq -c '.justification // null')"
    caveats_json="$(printf '%s\n' "${manifest}" | jq -c '.caveats // null')"
    applies_to_versioned="$(
      printf '%s\n' "${manifest}" |
        jq -r 'if .appliesToVersionedFormulae == true then "true" else "false" end'
    )"

    if [[ -z "${isotope_name}" ]]; then
      echo "Missing required manifest field 'name' in ${manifest_path}" >&2
      return 1
    fi
    if [[ -z "${modifies}" ]]; then
      echo "Missing required manifest field 'modifies' in ${manifest_path}" >&2
      return 1
    fi
    case "${modifies}" in
      brew:*)
        formula="${modifies#brew:}"
        ;;
      *)
        echo "Unsupported radioisotope modifies target ${modifies}" >&2
        echo "Skipping radioisotope ${isotope_name}" >&2
        continue
        ;;
    esac

    if ! release_json="$(homebrew_formula_release_json "${formula}")"; then
      echo "Skipping radioisotope ${isotope_name}: failed to resolve Homebrew formula ${formula}" >&2
      continue
    fi
    repo_name="${isotope_name#isotope:}"
    append_isotope_version_entry \
      "${entries_path}" \
      "${repo_name}" \
      "${radioisotopes_repo}" \
      "$(homebrew_formula_repository "${formula}")" \
      "${release_json}" \
      "${isotope_name}" \
      "" \
      "${modifies}" \
      "" \
      "${justification_json}" \
      "${caveats_json}" \
      "${applies_to_versioned}"

    if [[ "${applies_to_versioned}" != "true" ]]; then
      continue
    fi

    while IFS= read -r versioned_formula; do
      [[ -n "${versioned_formula}" ]] || continue
      if [[ -d "${radioisotopes_dir}/${versioned_formula}" ]]; then
        continue
      fi
      if ! versioned_release_json="$(homebrew_formula_release_json "${versioned_formula}")"; then
        echo "Skipping radioisotope ${isotope_name}: failed to resolve Homebrew formula ${versioned_formula}" >&2
        continue
      fi
      append_isotope_version_entry \
        "${entries_path}" \
        "${versioned_formula}" \
        "${radioisotopes_repo}" \
        "$(homebrew_formula_repository "${versioned_formula}")" \
        "${versioned_release_json}" \
        "isotope:${versioned_formula}" \
        "" \
        "brew:${versioned_formula}" \
        "" \
        "${justification_json}" \
        "${caveats_json}" \
        "false"
    done < <(homebrew_versioned_formulae "${formula}")
  done
}

run_manifest_build() {
  local repo_dir="$1"
  local tag="$2"
  local version="$3"
  local manifest_path="${repo_dir}/automic-vault.yml"
  local build_script

  build_script="$(manifest_field "${manifest_path}" "build")"
  if [[ -z "${build_script}" ]]; then
    echo "Missing required manifest field 'build' in ${manifest_path}" >&2
    return 1
  fi

  (
    cd "${repo_dir}"
    CI="${CI:-true}" TAG="${tag}" VERSION="${version}" bash -euo pipefail -c "${build_script}"
  )
}

process_repo() {
  local repo_name="$1"
  local fork_repo="${org}/${repo_name}"
  local repo_dir="${clone_root}/${repo_name}"
  local repo_json source_json source_kind upstream_repo upstream_default release_json tag
  local upstream_name version isotope_output archive_name archive_path release_url
  local current_branch publish_status

  ensure_clone "${repo_name}" "${repo_dir}"

  repo_json="$(gh api "/repos/${fork_repo}")"
  source_json="$(repo_source_json "${repo_name}" "${repo_json}" "${repo_dir}")"
  source_kind="$(printf '%s\n' "${source_json}" | jq -r '.kind')"

  if [[ "${source_kind}" == "unsupported" ]]; then
    echo "Skipping ${fork_repo}: $(printf '%s\n' "${source_json}" | jq -r '.reason')"
    return 0
  fi

  upstream_repo="$(printf '%s\n' "${source_json}" | jq -r '.upstreamRepo')"
  upstream_default="$(printf '%s\n' "${source_json}" | jq -r '.upstreamDefault')"
  release_json="$(printf '%s\n' "${source_json}" | jq -c '.release')"

  if [[ -z "${upstream_repo}" || "${upstream_repo}" == "null" ]]; then
    echo "Skipping ${fork_repo}: upstream repository is unavailable"
    return 0
  fi

  if [[ "${source_kind}" == "github" ]]; then
    set_upstream_remote "${repo_dir}" "${upstream_repo}"
  fi

  if [[ -z "${release_json}" || "${release_json}" == "null" ]]; then
    echo "Skipping ${fork_repo}: ${upstream_repo} has no release source"
    return 0
  fi

  tag="$(printf '%s\n' "${release_json}" | jq -r '.tag_name')"
  release_url="$(printf '%s\n' "${release_json}" | jq -r '.html_url')"

  if [[ -z "${tag}" || "${tag}" == "null" ]]; then
    echo "Skipping ${fork_repo}: latest release did not include a tag"
    return 0
  fi

  if release_exists_on_fork "${fork_repo}" "${tag}"; then
    echo "Skipping ${fork_repo}: release ${tag} already exists"
    return 0
  fi

  upstream_name="$(printf '%s\n' "${source_json}" | jq -r '.upstreamName')"
  version="$(sanitize_version "${tag}")"
  archive_name="${upstream_name}-${version}.tgz"
  archive_path="${repo_dir}/isotopes/${repo_name}/${archive_name}"

  echo "New upstream release for ${fork_repo}: ${upstream_repo} ${tag}"

  if [[ "${dry_run}" == "true" ]]; then
    if [[ "${source_kind}" == "github" ]]; then
      echo "Would fetch upstream tag ${tag} if missing"
      echo "Would rebase onto upstream tag ${tag}"
    else
      echo "Would fetch origin ${upstream_default} without tags"
      echo "Would rebase onto origin ${upstream_default}"
    fi
    echo "Would move tag ${tag} to HEAD"
    echo "Would push the branch and force-push tag ${tag} to origin"
    echo "Would run build from ${repo_dir}/automic-vault.yml"
    echo "Would rename out.tgz to ${archive_path}"
    publish_release \
      "${fork_repo}" \
      "${tag}" \
      "${upstream_repo}" \
      "${release_url}" \
      "${archive_path}"
    return 0
  fi

  if [[ "${source_kind}" == "github" ]]; then
    fetch_release_tag_if_missing "${repo_dir}" upstream "${tag}"
    rebase_onto_ref "${repo_dir}" "refs/tags/${tag}"
  else
    fetch_branch_without_tags "${repo_dir}" origin "${upstream_default}"
    rebase_onto_remote_branch "${repo_dir}" origin "${upstream_default}"
  fi
  move_release_tag_to_head "${repo_dir}" "${tag}"

  run_manifest_build "${repo_dir}" "${tag}" "${version}"

  if ! isotope_output="$(find_isotope_output "${repo_dir}" "${repo_name}")"; then
    echo "Expected build artifact was not created at either:" >&2
    echo "  ${repo_dir}/isotopes/${repo_name}/out.tgz" >&2
    echo "  ${repo_dir}/out.tgz" >&2
    return 1
  fi

  mkdir -p "$(dirname "${archive_path}")"
  mv -f "${isotope_output}" "${archive_path}"

  current_branch="$(git -C "${repo_dir}" branch --show-current)"
  if [[ -n "${current_branch}" ]]; then
    set +e
    run_publish_command git -C "${repo_dir}" push origin "HEAD:${current_branch}" --force-with-lease
    publish_status=$?
    set -e
    if [[ "${RUN_PUBLISH_COMMAND_AUTH_UNAVAILABLE}" -eq 1 ]]; then
      echo "Skipping remote isotope publication for ${fork_repo}: GitHub authentication was unavailable after building ${archive_name}" >&2
      return 0
    elif [[ "${publish_status}" -ne 0 ]]; then
      return "${publish_status}"
    fi
  fi

  set +e
  run_publish_command git -C "${repo_dir}" push origin "+refs/tags/${tag}:refs/tags/${tag}"
  publish_status=$?
  set -e
  if [[ "${RUN_PUBLISH_COMMAND_AUTH_UNAVAILABLE}" -eq 1 ]]; then
    echo "Skipping GitHub release publish for ${fork_repo}: tag push could not authenticate after building ${archive_name}" >&2
    return 0
  elif [[ "${publish_status}" -ne 0 ]]; then
    return "${publish_status}"
  fi

  set +e
  run_publish_command \
    publish_release \
    "${fork_repo}" \
    "${tag}" \
    "${upstream_repo}" \
    "${release_url}" \
    "${archive_path}"
  publish_status=$?
  set -e
  if [[ "${RUN_PUBLISH_COMMAND_AUTH_UNAVAILABLE}" -eq 1 ]]; then
    echo "Skipping GitHub release publish for ${fork_repo}: release creation could not authenticate after building ${archive_name}" >&2
    return 0
  elif [[ "${publish_status}" -ne 0 ]]; then
    return "${publish_status}"
  fi
}

update_radioisotopes_clone

all_repo_names="$(
  gh repo list "${org}" \
    --limit 1000 \
    --json isFork,name \
    --jq '.[] | select(.isFork) | .name'
)"
repo_names="${all_repo_names}"

if [[ -n "${only_repo}" ]]; then
  repo_names="$(printf '%s\n' "${all_repo_names}" | awk -v repo="${only_repo}" '$0 == repo')"
fi

if [[ -z "${repo_names}" ]]; then
  echo "No repositories found for ${org}" >&2
  exit 1
fi

while IFS= read -r repo_name; do
  [[ -n "${repo_name}" ]] || continue
  if [[ "${skip_builds}" == "true" ]]; then
    echo "Skipping build/release for ${repo_name}"
    continue
  fi

  process_repo "${repo_name}"
done <<<"${repo_names}"

if [[ "${dry_run}" == "true" ]]; then
  echo "Would write ${isotope_versions_path}"
else
  generate_isotope_versions_json "${all_repo_names}"
fi
