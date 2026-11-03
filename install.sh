#!/usr/bin/env bash
# Install the earmark CLI and link the earmark skill into each agent CLI's skills directory.
#
#   ./install.sh                  CLI + links in ~/.agents/skills, ~/.claude/skills, ~/.gemini/config/skills
#   ./install.sh --project DIR    CLI + links in DIR/.agents/skills and DIR/.claude/skills
#   ./install.sh --uninstall      remove what this script installed (combine with --project DIR)
#   ./install.sh --dry-run        print the actions without changing anything
#   ./install.sh --no-cli         links only (same as EARMARK_INSTALL_SKIP_CLI=1)
#
# Idempotent. Touches only the paths above, plus the CLI install through uv, pipx or a
# ~/.local/bin/earmark symlink to the checkout launcher. Existing directories are never replaced.
set -eu

REPO=$(cd "$(dirname "$0")" && pwd -P)
SKILL="$REPO/skills/earmark"
LAUNCHER="$REPO/earmark"
DIST=earmark-skill

mode=install
dry=0
project=
skip_cli=0
case "${EARMARK_INSTALL_SKIP_CLI:-0}" in 0 | "") ;; *) skip_cli=1 ;; esac

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall) mode=uninstall ;;
    --dry-run) dry=1 ;;
    --no-cli) skip_cli=1 ;;
    --project)
      [ $# -ge 2 ] || { echo "error: --project needs a directory" >&2; exit 1; }
      project=$2
      shift
      ;;
    --project=*) project=${1#--project=} ;;
    -h | --help) usage; exit 0 ;;
    *) echo "error: unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

[ -f "$SKILL/SKILL.md" ] || { echo "error: $SKILL/SKILL.md not found; run from an earmark-skill checkout" >&2; exit 1; }
[ -n "${HOME:-}" ] || { echo "error: HOME is not set" >&2; exit 1; }

if [ -n "$project" ]; then
  [ -d "$project" ] || { echo "error: project directory not found: $project" >&2; exit 1; }
  project=$(cd "$project" && pwd -P)
  LINKS=("$project/.agents/skills/earmark" "$project/.claude/skills/earmark")
else
  LINKS=("$HOME/.agents/skills/earmark" "$HOME/.claude/skills/earmark" "$HOME/.gemini/config/skills/earmark")
fi

status=0
prefix=
[ "$dry" = 1 ] && prefix="would "

say() { printf '%s%s\n' "$prefix" "$*"; }
run() {
  say "run   $*"
  [ "$dry" = 1 ] || "$@"
}

# link PATH TARGET: create or repair one symlink; never replaces a real file or directory.
link() {
  local path=$1 target=$2 current
  if [ -L "$path" ]; then
    current=$(readlink "$path")
    if [ "$current" = "$target" ]; then
      printf 'ok    %s (already linked)\n' "$path"
      return
    fi
    say "link  $path -> $target (was -> $current)"
    [ "$dry" = 1 ] || { rm -f "$path" && ln -s "$target" "$path"; }
  elif [ -e "$path" ]; then
    printf 'skip  %s exists and is not a symlink; remove it to link this checkout\n' "$path" >&2
    status=1
  else
    say "link  $path -> $target"
    [ "$dry" = 1 ] || { mkdir -p "$(dirname "$path")" && ln -s "$target" "$path"; }
  fi
}

# unlink PATH TARGET: remove the symlink only when it points at TARGET.
unlink_ours() {
  local path=$1 target=$2
  if [ -L "$path" ] && [ "$(readlink "$path")" = "$target" ]; then
    say "rm    $path"
    [ "$dry" = 1 ] || rm -f "$path"
  elif [ -e "$path" ] || [ -L "$path" ]; then
    printf 'skip  %s does not point at this checkout\n' "$path" >&2
  else
    printf 'ok    %s (absent)\n' "$path"
  fi
}

has() { command -v "$1" >/dev/null 2>&1; }

install_cli() {
  if [ "$skip_cli" = 1 ]; then
    printf 'skip  CLI install\n'
    return
  fi
  if has uv; then
    run uv tool install --force --reinstall "$REPO"
  elif has pipx; then
    run pipx install --force "$REPO"
  else
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null ||
      printf 'note  the earmark launcher needs python3 3.10 or newer on PATH\n' >&2
    link "$HOME/.local/bin/earmark" "$LAUNCHER"
  fi
  if [ "$dry" = 0 ] && ! has earmark; then
    printf 'note  earmark is not on PATH yet; add ~/.local/bin to PATH\n' >&2
  fi
}

uninstall_cli() {
  if [ "$skip_cli" = 1 ]; then
    printf 'skip  CLI uninstall\n'
    return
  fi
  if has uv && uv tool list 2>/dev/null | grep -q "^$DIST "; then
    run uv tool uninstall "$DIST"
  fi
  if has pipx && pipx list --short 2>/dev/null | grep -q "^$DIST "; then
    run pipx uninstall "$DIST"
  fi
  if [ -L "$HOME/.local/bin/earmark" ]; then
    unlink_ours "$HOME/.local/bin/earmark" "$LAUNCHER"
  fi
}

if [ "$mode" = install ]; then
  install_cli
  for path in "${LINKS[@]}"; do link "$path" "$SKILL"; done
else
  for path in "${LINKS[@]}"; do unlink_ours "$path" "$SKILL"; done
  uninstall_cli
fi
exit $status
