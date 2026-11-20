#!/bin/sh
# Installs the portofion agent skill and saves the API key.
#
#   curl -fsSL {{BASE_URL}}/skill/install.sh | PORTOFION_API_KEY=pfn_... sh
#
# PORTOFION_SKILL_DIRS  space-separated skill folders (default: ~/.claude/skills, plus ~/.codex/skills if ~/.codex exists)
# PORTOFION_CONFIG      credentials file (default: ~/.portofion/config.json)
set -eu

base="{{BASE_URL}}"
key="${PORTOFION_API_KEY:-}"

tmp="$(mktemp -d 2>/dev/null || mktemp -d -t portofion)"
trap 'rm -rf "$tmp"' EXIT INT TERM

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$base/skill/portofion.tar.gz" -o "$tmp/skill.tgz"
else
  wget -qO "$tmp/skill.tgz" "$base/skill/portofion.tar.gz"
fi

dirs="${PORTOFION_SKILL_DIRS:-}"
if [ -z "$dirs" ]; then
  dirs="$HOME/.claude/skills"
  if [ -d "$HOME/.codex" ]; then dirs="$dirs $HOME/.codex/skills"; fi
fi
first=""
for d in $dirs; do
  mkdir -p "$d"
  tar -xzf "$tmp/skill.tgz" -C "$d"
  echo "skill installed: $d/portofion"
  if [ -z "$first" ]; then first="$d/portofion"; fi
done

if [ -n "$key" ]; then
  cfg="${PORTOFION_CONFIG:-$HOME/.portofion/config.json}"
  mkdir -p "$(dirname "$cfg")"
  (umask 077 && printf '{"url": "%s", "api_key": "%s"}\n' "$base" "$key" > "$cfg")
  echo "credentials saved: $cfg"
fi

py=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; then
    py="$c"
    break
  fi
done
if [ -z "$py" ]; then
  echo "note: the skill's script needs Python 3.8+, which was not found on PATH"
elif [ -n "$key" ]; then
  "$py" "$first/scripts/portofion.py" whoami || echo "warning: the key check failed (see above)"
fi
