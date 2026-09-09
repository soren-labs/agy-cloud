#!/usr/bin/env bash
# agy-worker — minimal "cloud agent" emulation (Cursor/Hoplite-style) built from:
#   brain   : Antigravity CLI (`agy`, Pro-subscription OAuth token, auto-refreshed by agy itself)
#   sandbox : clean Docker container, non-root (stand-in for a GCP VPS / any Linux box)
#   surface : GitHub branch + PR; follow-ups resume the same agy conversation
#
#   worker.sh run      <agent-id> <owner/repo> <base-branch> <agy-model> <task>
#   worker.sh followup <agent-id> <follow-up prompt>
#   worker.sh review   <agent-id> <owner/repo> <pr-number> <agy-model>
#   worker.sh status   <agent-id>        worker.sh ls
#
# Env : GH_TOKEN        required  (export GH_TOKEN=$(gh auth token))
#       AGY_TEST_CMD    optional  e.g. 'python3 -m unittest discover -s tests -v' — runs after every coding turn, result posted to the PR
#       AGY_TOKEN_FILE  optional  default ~/.gemini/antigravity-cli/antigravity-oauth-token; point at another account's token = another subscription
# One state dir == one sandbox == one Antigravity account.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_ROOT="${AGY_WORKER_STATE:-$HERE/state}"
IMAGE="${AGY_WORKER_IMAGE:-agy-worker:base}"
AGY_BIN="${AGY_BIN:-$HOME/.local/bin/agy}"
AGY_TOKEN_FILE="${AGY_TOKEN_FILE:-$HOME/.gemini/antigravity-cli/antigravity-oauth-token}"
AGY_SETTINGS_FILE="${AGY_SETTINGS_FILE:-$HOME/.gemini/antigravity-cli/settings.json}"

# ================= host side =================
build_image() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 && return 0
  docker build -q -t "$IMAGE" - <<'EOF'
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git curl jq python3 && rm -rf /var/lib/apt/lists/*
USER ubuntu
WORKDIR /home/ubuntu
EOF
}

ensure_state() {  # $1 agent-id -> prints state dir; seeds Antigravity credentials once
  local st="$STATE_ROOT/$1" cli="$STATE_ROOT/$1/.gemini/antigravity-cli"
  mkdir -p "$cli"
  [[ -f "$cli/antigravity-oauth-token" ]] || install -m 600 "$AGY_TOKEN_FILE" "$cli/antigravity-oauth-token"
  [[ -f "$cli/settings.json" || ! -f "$AGY_SETTINGS_FILE" ]] || cp "$AGY_SETTINGS_FILE" "$cli/settings.json"
  printf '%s\n' "$st"
}

docker_run() {  # $1 state dir, rest -> inner args
  local st="$1"; shift
  : "${GH_TOKEN:?export GH_TOKEN=\$(gh auth token) first}"
  docker run --rm \
    -e HOME=/home/ubuntu -e GH_TOKEN -e AGY_TEST_CMD \
    -v "$st:/home/ubuntu" \
    -v "$AGY_BIN:/usr/local/bin/agy:ro" \
    -v "$HERE/worker.sh:/opt/worker.sh:ro" \
    "$IMAGE" bash /opt/worker.sh inner "$@"
}

cmd_run() {
  local id="$1" repo="$2" base="$3" model="$4" task="$5" slug branch st
  slug="$(printf '%s' "$task" | head -n1 | tr -cs 'A-Za-z0-9' '-' | tr 'A-Z' 'a-z' | cut -c1-40 | sed 's/^-//;s/-$//')"
  branch="agy/${slug}-$(head -c 2 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  build_image; st="$(ensure_state "$id")"
  docker_run "$st" new "$repo" "$base" "$model" "$branch" "$task"
}

cmd_followup() {
  local id="$1" prompt="$2" st="$STATE_ROOT/$1"
  [[ -f "$st/agent.json" ]] || { echo "unknown agent: $id" >&2; exit 1; }
  docker_run "$st" followup "$prompt"
}

cmd_review() {
  local id="$1" repo="$2" pr="$3" model="$4" st
  build_image; st="$(ensure_state "$id")"
  docker_run "$st" review "$repo" "$pr" "$model"
}

cmd_status() { jq . "$STATE_ROOT/$1/agent.json"; }
cmd_ls() {
  local d
  for d in "$STATE_ROOT"/*/; do
    [[ -f "$d/agent.json" ]] || continue
    jq -c --arg id "$(basename "$d")" '{id:$id, mode:(.mode//"code"), repo, branch, pr_url, runs:(.runs|length), last:(.runs[-1].status//null)}' "$d/agent.json"
  done
}

# ================= container side =================
gh_api() {  # $1 method, $2 url ; body on stdin when -d @- given in $3..
  curl -sS -X "$1" -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github+json" "$2" "${@:3}"
}

run_agy() {  # $1 model, $2 conversation-id (may be empty), $3 prompt, $4 out.json, $5 stderr
  local args=(--output-format json --dangerously-skip-permissions --print-timeout 30m --add-dir "$HOME/ws")
  [[ -n "$1" && "$1" != null ]] && args+=(--model "$1")
  [[ -n "$2" ]] && args+=(--conversation "$2")
  agy "${args[@]}" --print="$3" > "$4" 2> "$5"
}

git_setup() {
  git config --global user.name  "agy-worker"
  git config --global user.email "agy-worker@users.noreply.github.com"
  git config --global credential.helper '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'
}

inner_code() {  # new | followup
  local mode="$1"; shift
  local meta="$HOME/agent.json" ws="$HOME/ws"
  local repo base model branch conv prompt usertext
  if [[ "$mode" == new ]]; then
    repo="$1" base="$2" model="$3" branch="$4" usertext="$5"
    rm -rf "$ws"
    git clone -q --depth 50 --branch "$base" "https://github.com/$repo.git" "$ws"
    git -C "$ws" checkout -q -b "$branch"
    conv=""
    jq -n --arg repo "$repo" --arg base "$base" --arg model "$model" --arg branch "$branch" \
      '{mode:"code",repo:$repo,base:$base,model:$model,branch:$branch,conversation_id:null,pr_url:null,runs:[]}' > "$meta"
    prompt="You are an autonomous coding agent. The git repository $repo is checked out at $ws (branch $branch) and it is your current working directory. Only create/modify files inside $ws. Do NOT run git commit or git push (the harness commits and opens the PR for you). Run the relevant tests yourself before finishing. When done, reply with a concise summary of what you changed and how you verified it.

TASK:
$usertext"
  else
    usertext="$1"
    repo=$(jq -r .repo "$meta"); base=$(jq -r .base "$meta"); model=$(jq -r .model "$meta")
    branch=$(jq -r .branch "$meta"); conv=$(jq -r '.conversation_id // empty' "$meta")
    git -C "$ws" pull -q --ff-only origin "$branch" || true   # pick up reviewer commits on the PR branch
    prompt="FOLLOW-UP on the same task. Repository still at $ws, branch $branch, your cwd. Same rules: only edit inside $ws, no git commit/push, run the tests before finishing. Reply with a concise summary when done.

FOLLOW-UP:
$usertext"
  fi

  local ts out err rc=0
  ts=$(date +%Y%m%dT%H%M%S); out="$HOME/run-$ts.json"; err="$HOME/agy-$ts.stderr"
  cd "$ws"
  run_agy "$model" "$conv" "$prompt" "$out" "$err" || rc=$?

  local status resp newconv
  status=$(jq -r '.status // "UNKNOWN"' "$out" 2>/dev/null || echo PARSE_ERROR)
  newconv=$(jq -r '.conversation_id // empty' "$out" 2>/dev/null || true)
  resp=$(jq -r '.response // ""' "$out" 2>/dev/null || true)
  [[ -n "$newconv" ]] && conv="$newconv"

  # ---- optional integration-test gate (CI-lite, runs in the same sandbox) ----
  local test_status=skipped test_rc=0 tests_md="_tests: skipped (AGY_TEST_CMD not set)_"
  if [[ -n "${AGY_TEST_CMD:-}" ]]; then
    set +e; bash -c "$AGY_TEST_CMD" > "$HOME/test-$ts.log" 2>&1; test_rc=$?; set -e
    if [[ $test_rc -eq 0 ]]; then
      test_status=passed; tests_md="✅ \`$AGY_TEST_CMD\` passed"
    else
      test_status=failed
      tests_md=$(printf '❌ `%s` failed (exit %s)\n```\n%s\n```' "$AGY_TEST_CMD" "$test_rc" "$(tail -n 25 "$HOME/test-$ts.log")")
    fi
  fi

  # ---- commit + push whatever the agent produced ----
  local title sha pushed=false upstream="origin/$branch"
  title="agy: $(printf '%s' "$usertext" | head -n1 | cut -c1-64)"
  git rev-parse --verify -q "$upstream" >/dev/null 2>&1 || upstream="origin/$base"
  if [[ -n "$(git status --porcelain)" ]]; then
    git add -A
    git commit -q -F - <<EOF
$title

Agent: antigravity-cli ($model) via agy-worker
Conversation: ${conv:-n/a}
Tests: $test_status

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
  fi
  if [[ "$(git rev-list --count "$upstream..HEAD")" -gt 0 ]]; then
    git push -q -u origin "$branch"; pushed=true
  fi
  sha=$(git rev-parse --short HEAD)

  # ---- open PR once; later turns comment on it ----
  local api="https://api.github.com/repos/$repo" pr_url footer body
  pr_url=$(jq -r '.pr_url // empty' "$meta")
  footer=$(printf '\n\n%s\n\n---\n_Agent: Antigravity CLI `%s` · conversation `%s` · sandbox: agy-worker container_\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)' "$tests_md" "$model" "$conv")
  if [[ -z "$pr_url" && "$pushed" == true ]]; then
    body="${resp:0:3000}$footer"
    pr_url=$(jq -n --arg t "$title" --arg h "$branch" --arg b "$base" --arg body "$body" '{title:$t,head:$h,base:$b,body:$body}' \
      | gh_api POST "$api/pulls" -d @- | jq -r '.html_url // empty')
  elif [[ -n "$pr_url" ]]; then
    body="**Follow-up:** ${usertext:0:300}

${resp:0:3000}$footer"
    jq -n --arg b "$body" '{body:$b}' | gh_api POST "$api/issues/${pr_url##*/}/comments" -d @- >/dev/null || true
  fi

  # ---- persist metadata ----
  local runjson tmp
  runjson=$(jq -c '{duration_seconds,num_turns,usage}' "$out" 2>/dev/null) || runjson='{}'
  tmp=$(mktemp)
  jq --arg conv "$conv" --arg pr "$pr_url" --arg mode "$mode" --arg status "$status" --arg sha "$sha" --arg tests "$test_status" \
     --arg log "$(basename "$out")" --argjson rc "$rc" --argjson run "$runjson" --argjson pushed "$pushed" \
     '.conversation_id = (if $conv=="" then .conversation_id else $conv end)
      | .pr_url = (if $pr=="" then .pr_url else $pr end)
      | .runs += [{mode:$mode,status:$status,rc:$rc,tests:$tests,pushed:$pushed,commit:$sha,log:$log} + $run]' "$meta" > "$tmp"
  mv "$tmp" "$meta"

  jq -c --arg status "$status" --arg sha "$sha" --arg pushed "$pushed" --arg tests "$test_status" --arg resp "${resp:0:300}" \
     '{agent_status:$status, tests:$tests, conversation_id, branch, pushed:$pushed, commit:$sha, pr_url, response_head:$resp}' "$meta"
  [[ "$rc" -eq 0 ]]
}

inner_review() {  # read-only review of an existing PR, posted as a PR review comment
  local repo="$1" prnum="$2" model="$3"
  local meta="$HOME/agent.json" ws="$HOME/ws" api="https://api.github.com/repos/$repo"
  local pr headref baseref title diff prompt ts out err rc=0 status conv resp review_url=""
  pr=$(gh_api GET "$api/pulls/$prnum")
  headref=$(jq -r .head.ref <<<"$pr"); baseref=$(jq -r .base.ref <<<"$pr"); title=$(jq -r .title <<<"$pr")
  rm -rf "$ws"
  git clone -q --depth 100 --branch "$baseref" "https://github.com/$repo.git" "$ws"
  git -C "$ws" fetch -q origin "pull/$prnum/head:pr-$prnum"
  git -C "$ws" checkout -q "pr-$prnum"
  curl -sS -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/vnd.github.diff" "$api/pulls/$prnum" > "$HOME/pr-$prnum.diff"
  diff=$(head -c 60000 "$HOME/pr-$prnum.diff")
  jq -n --arg repo "$repo" --argjson pr "$prnum" --arg model "$model" --arg branch "$headref" --arg base "$baseref" \
     '{mode:"review",repo:$repo,pr_number:$pr,branch:$branch,base:$base,model:$model,conversation_id:null,pr_url:null,runs:[]}' > "$meta"
  prompt="You are a senior code reviewer. Review pull request #$prnum (\"$title\") of $repo. The PR branch is checked out at $ws (your cwd). You may run the existing test suite to verify claims, but do NOT modify, commit or push anything. Reply in GitHub-flavored markdown with sections: **Summary** (2-3 lines), **Issues** (file:line — severity — why; or 'none found'), **Test coverage gaps**, **Verdict** (LGTM / LGTM with nits / Needs changes).

Unified diff vs $baseref:
\`\`\`diff
$diff
\`\`\`"
  ts=$(date +%Y%m%dT%H%M%S); out="$HOME/run-$ts.json"; err="$HOME/agy-$ts.stderr"
  cd "$ws"
  run_agy "$model" "" "$prompt" "$out" "$err" || rc=$?
  git checkout -q -- . 2>/dev/null || true; git clean -fdq || true   # safety net: reviews never change the tree
  status=$(jq -r '.status // "UNKNOWN"' "$out" 2>/dev/null || echo PARSE_ERROR)
  conv=$(jq -r '.conversation_id // empty' "$out" 2>/dev/null || true)
  resp=$(jq -r '.response // ""' "$out" 2>/dev/null || true)
  if [[ -n "$resp" ]]; then
    review_url=$(jq -n --arg b "$(printf '%s\n\n---\n_Reviewed by Antigravity CLI `%s` · conversation `%s` · sandbox: agy-worker container_' "$resp" "$model" "$conv")" \
                    '{body:$b,event:"COMMENT"}' | gh_api POST "$api/pulls/$prnum/reviews" -d @- | jq -r '.html_url // empty')
  fi
  local runjson tmp
  runjson=$(jq -c '{duration_seconds,num_turns,usage}' "$out" 2>/dev/null) || runjson='{}'
  tmp=$(mktemp)
  jq --arg conv "$conv" --arg url "$review_url" --arg status "$status" --argjson rc "$rc" --argjson run "$runjson" --arg log "$(basename "$out")" \
     '.conversation_id = (if $conv=="" then .conversation_id else $conv end)
      | .pr_url = (if $url=="" then .pr_url else $url end)
      | .runs += [{mode:"review",status:$status,rc:$rc,log:$log} + $run]' "$meta" > "$tmp"
  mv "$tmp" "$meta"
  jq -c --arg status "$status" --arg resp "${resp:0:300}" '{agent_status:$status, conversation_id, pr_number, review_url:.pr_url, response_head:$resp}' "$meta"
  [[ "$rc" -eq 0 ]]
}

inner() {
  git_setup
  case "$1" in
    new|followup) inner_code "$@" ;;
    review)       shift; inner_review "$@" ;;
    *) echo "bad inner mode: $1" >&2; exit 2 ;;
  esac
}

case "${1:-}" in
  run)      shift; cmd_run "$@" ;;
  followup) shift; cmd_followup "$@" ;;
  review)   shift; cmd_review "$@" ;;
  status)   shift; cmd_status "$@" ;;
  ls)       cmd_ls ;;
  inner)    shift; inner "$@" ;;
  *) sed -n '2,15p' "$0"; exit 2 ;;
esac
