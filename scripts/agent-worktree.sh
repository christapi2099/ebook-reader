#!/usr/bin/env bash
# Per-agent git worktrees for this repo.
#
# WHY THIS EXISTS
# Several agents editing one working tree is the root cause of the work-loss
# incident this repo already had: `git stash push` on a file two agents co-owned
# reverted it to HEAD and destroyed both agents' edits. Any state-changing git
# command in a shared tree can do that, and it is not really preventable - a hook
# can veto ref changes, but `git checkout -- <path>` and `git restore` change no
# refs at all, so a hook would give partial cover while risking breaking git for
# every agent at once.
#
# A worktree removes the shared working tree instead. Each agent gets its own
# checkout with its own index and HEAD, so its git commands cannot touch anyone
# else's files; only commits and merges are shared, and those are deliberate.
#
# THE CATCH, AND HOW THIS HANDLES IT
# A fresh worktree has none of the gitignored state a working checkout needs.
# Here that is substantial: backend/venv (6.8 GB), backend/.venv (6.5 GB),
# .uv-cache (1.1 GB) and frontend/node_modules (310 MB). So instead of copying
# gigabytes, this symlinks them in - they are read-mostly and safe to share, and
# the alternative is an unusable worktree.
#
# WHAT ISOLATION DOES AND DOES NOT COVER (verified by experiment, not assumed)
# Covered: each worktree has its own working tree and index, so `git stash`,
# `git checkout --`, `git restore` and `git reset` in one worktree cannot touch
# another worktree's files. That is the failure mode this exists for and it is
# genuinely fixed - I stashed and destroyed a file inside a worktree and the main
# tree was unaffected.
# NOT covered: `refs/stash` lives in the SHARED git dir, not per worktree. A stash
# created in a worktree shows up in `git stash list` everywhere, so popping it
# from the wrong worktree would apply that worktree's changes in the wrong place.
# Treat the stash as global: prefer committing to stashing, and never pop a stash
# you did not create.
#
# Usage:
#   scripts/agent-worktree.sh add <name> [base-ref]   create a worktree
#   scripts/agent-worktree.sh list                    list worktrees
#   scripts/agent-worktree.sh remove <name>           remove it (keeps the branch)
#   scripts/agent-worktree.sh paths <name>            print the paths to hand an agent
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
WT_DIR="$ROOT/.worktrees"

# Gitignored directories a worktree needs to be usable, as relative paths.
LINKED_STATE=(
  "backend/venv"
  "backend/.venv"
  ".uv-cache"
  "frontend/node_modules"
)

usage() { sed -n '2,30p' "$0"; exit 2; }

cmd_add() {
  local name="${1:-}" base="${2:-HEAD}"
  [ -z "$name" ] && usage
  local dest="$WT_DIR/$name"
  local branch="agent/$name"

  [ -e "$dest" ] && { echo "already exists: $dest" >&2; exit 1; }
  mkdir -p "$WT_DIR"

  if git show-ref --verify --quiet "refs/heads/$branch"; then
    echo "attaching worktree to existing branch $branch"
    git worktree add "$dest" "$branch"
  else
    git worktree add -b "$branch" "$dest" "$base"
  fi

  # Share the heavy gitignored state rather than copying gigabytes of it.
  local rel src
  for rel in "${LINKED_STATE[@]}"; do
    src="$ROOT/$rel"
    if [ -e "$src" ]; then
      mkdir -p "$(dirname "$dest/$rel")"
      ln -sfn "$src" "$dest/$rel"
      echo "linked  $rel"
    else
      echo "skipped $rel (not present in the main checkout)"
    fi
  done

  echo
  echo "worktree ready:"
  echo "  path   $dest"
  echo "  branch $branch"
  echo
  echo "Hand an agent THAT path as its working directory, and tell it:"
  echo "  - run Python as $dest/backend/venv/bin/python (the symlink is real)"
  echo "  - it must not run 'git stash', 'git checkout --', 'git restore' or"
  echo "    'git reset' unless it created the worktree's own changes"
  echo "  - commits land on $branch; the parent agent merges them deliberately"
}

cmd_list() { git worktree list; }

cmd_remove() {
  local name="${1:-}"; [ -z "$name" ] && usage
  local dest="$WT_DIR/$name"
  # Unlink the shared state first: `worktree remove` must not follow the symlinks
  # and try to delete the real venv or node_modules.
  local rel
  for rel in "${LINKED_STATE[@]}"; do
    [ -L "$dest/$rel" ] && rm -f "$dest/$rel"
  done
  git worktree remove "$dest" --force
  echo "removed $dest (branch agent/$name kept)"
}

cmd_paths() {
  local name="${1:-}"; [ -z "$name" ] && usage
  echo "$WT_DIR/$name"
}

case "${1:-}" in
  add) shift; cmd_add "$@" ;;
  list) cmd_list ;;
  remove) shift; cmd_remove "$@" ;;
  paths) shift; cmd_paths "$@" ;;
  *) usage ;;
esac
