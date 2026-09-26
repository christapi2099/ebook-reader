#!/usr/bin/env bash
# Safety net for a repo being edited by several agents at once.
#
# The failure this exists for: an agent runs a state-changing git command
# (`git stash`, `git checkout --`, `git restore`, `git reset`, `git clean`) in the
# SHARED working tree and silently discards another agent's uncommitted work.
# That has already happened once here — `git stash push` of a file two agents
# co-owned reverted it to HEAD and destroyed both agents' edits.
#
# This does NOT try to block those commands. A hook that can veto git operations
# is a bigger hazard than the thing it prevents: if it misfires it breaks git for
# every agent at once. Instead it makes destruction RECOVERABLE, by taking cheap
# periodic snapshots that survive any git command.
#
# Two mechanisms, because they cover different things:
#   1. `git stash create` + `update-ref` — a real commit object of tracked and
#      staged changes. Tiny, precise, and does NOT touch the working tree, the
#      index, or the stash list. It does NOT capture untracked files.
#   2. An rsync copy of the source tree, which DOES capture untracked files —
#      and untracked is where most new work lives (new components, new test
#      files, a whole new doc).
#
# Usage:
#   scripts/agent-safety.sh snapshot      take a snapshot now
#   scripts/agent-safety.sh list          list snapshots
#   scripts/agent-safety.sh check         report whether anything was destroyed
#   scripts/agent-safety.sh restore <id>  restore a snapshot into the working tree
#   scripts/agent-safety.sh watch [secs]  snapshot on a loop (default 60s)
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel)"
SNAP_DIR="$ROOT/.snapshots"
KEEP="${AGENT_SAFETY_KEEP:-12}"

# Heavy or generated directories. The database is 800 MB and dominated every
# snapshot until it was excluded -- it is not source and is not restorable this
# way anyway.
EXCLUDES=(
  --exclude=.git --exclude=.snapshots
  --exclude=node_modules --exclude=venv --exclude=.venv --exclude=.uv-cache
  --exclude=.svelte-kit --exclude=__pycache__ --exclude='*.pyc'
  --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm'
  --exclude=test-results --exclude=playwright-report --exclude=blob-report
  --exclude=exports --exclude=uploads --exclude=.pytest_cache
)

snapshot() {
  mkdir -p "$SNAP_DIR"
  local stamp id dest
  stamp="$(date +%Y%m%d-%H%M%S)"
  dest="$SNAP_DIR/$stamp"

  # 1. Tracked + staged changes as a real commit object. `stash create` never
  #    mutates anything, so this is safe to run while agents are writing.
  id="$(git stash create "autosave $stamp" 2>/dev/null || true)"
  if [ -n "${id:-}" ]; then
    git update-ref "refs/autosave/$stamp" "$id" 2>/dev/null || true
    git update-ref refs/autosave/latest "$id" 2>/dev/null || true
  fi

  # 2. The whole source tree, untracked files included.
  mkdir -p "$dest"
  rsync -a --delete "${EXCLUDES[@]}" "$ROOT/" "$dest/" >/dev/null 2>&1

  printf '%s\n' "$stamp" > "$SNAP_DIR/LATEST"
  printf 'snapshot %s (tracked-ref %s)\n' "$stamp" "${id:-none}"

  # Rotate, keeping the newest $KEEP.
  local victims
  victims="$(ls -1d "$SNAP_DIR"/[0-9]* 2>/dev/null | sort | head -n -"$KEEP")"
  if [ -n "$victims" ]; then
    printf '%s\n' "$victims" | xargs rm -rf
    git for-each-ref --format='%(refname)' refs/autosave \
      | grep -v 'refs/autosave/latest$' | sort | head -n -"$KEEP" \
      | xargs -r -n1 git update-ref -d
  fi
}

cmd_list() {
  echo "=== tree snapshots (newest last) ==="
  ls -1d "$SNAP_DIR"/[0-9]* 2>/dev/null | sort | tail -5 || echo "(none)"
  echo
  echo "=== git autosave refs ==="
  git for-each-ref --sort=creatordate --format='%(refname) %(objectname:short)' refs/autosave 2>/dev/null | tail -5 || echo "(none)"
}

cmd_check() {
  local problems=0
  echo "=== stash list (must be empty) ==="
  if [ -n "$(git stash list)" ]; then
    git stash list; problems=1
  else
    echo "empty - OK"
  fi
  echo
  echo "=== destructive git commands in the reflog ==="
  # Creating a branch legitimately logs "checkout: moving from X to Y", which is
  # not destruction, so the one this branch was created with is excluded.
  local hits
  hits="$(git reflog --date=iso 2>/dev/null \
    | grep -iE 'stash|reset|restore|checkout: moving' \
    | grep -v 'from main to folders-and-pages' || true)"
  if [ -n "$hits" ]; then
    printf '%s\n' "$hits" | head -5
    problems=1
  else
    echo "none - OK"
  fi
  echo
  echo "=== newest snapshot ==="
  cat "$SNAP_DIR/LATEST" 2>/dev/null || echo "(none - run 'snapshot')"
  echo
  echo "=== uncommitted work at risk ==="
  git status --short | wc -l
  [ "$problems" -eq 0 ] && echo "VERDICT: clean" || echo "VERDICT: investigate the above"
  return "$problems"
}

cmd_restore() {
  local id="${1:-}"
  [ -z "$id" ] && { echo "usage: restore <snapshot-id|latest>" >&2; exit 2; }
  [ "$id" = "latest" ] && id="$(cat "$SNAP_DIR/LATEST")"
  local src="$SNAP_DIR/$id"
  [ -d "$src" ] || { echo "no such snapshot: $id" >&2; exit 1; }
  echo "restoring $id into $ROOT (does NOT delete files added since)"
  rsync -a "${EXCLUDES[@]}" "$src/" "$ROOT/" && echo "restored"
  echo "for tracked changes also available as: git stash apply refs/autosave/$id"
}

cmd_watch() {
  local every="${1:-60}"
  echo "snapshotting every ${every}s; stop with Ctrl-C"
  while true; do
    snapshot
    sleep "$every"
  done
}

case "${1:-check}" in
  snapshot) snapshot ;;
  list) cmd_list ;;
  check) cmd_check ;;
  restore) shift; cmd_restore "$@" ;;
  watch) shift; cmd_watch "$@" ;;
  *) sed -n '2,30p' "$0"; exit 2 ;;
esac
