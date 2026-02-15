#!/usr/bin/env bash
set -euo pipefail

strict=0
if [[ "${1:-}" == "--strict" ]]; then
  strict=1
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

threshold_mb="${HYGIENE_THRESHOLD_MB:-25}"
threshold_kb=$((threshold_mb * 1024))

printf "Repo hygiene report (%s)\n" "$(date '+%Y-%m-%d %H:%M:%S')"
printf "Threshold: %s MB\n\n" "$threshold_mb"

untracked_top="$(git ls-files --others --exclude-standard | awk -F/ 'NF {print $1}' | sort -u)"

if [[ -z "$untracked_top" ]]; then
  echo "No untracked files (excluding ignored patterns)."
else
  echo "Untracked top-level entries:"
  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    if [[ -e "$entry" ]]; then
      size_kb="$(du -sk "$entry" | awk '{print $1}')"
      printf "%8s KB  %s\n" "$size_kb" "$entry"
    fi
  done <<< "$untracked_top" | sort -n
fi

echo

over=0
while IFS= read -r entry; do
  [[ -n "$entry" ]] || continue
  [[ -e "$entry" ]] || continue
  size_kb="$(du -sk "$entry" | awk '{print $1}')"
  if (( size_kb >= threshold_kb )); then
    over=1
    size_mb="$(awk -v kb="$size_kb" 'BEGIN { printf "%.1f", kb/1024 }')"
    printf "Large untracked artifact: %s (%s MB)\n" "$entry" "$size_mb"
  fi
done <<< "$untracked_top"

echo
pyproject_version="$(awk -F'"' '/^version = "/ {print $2; exit}' pyproject.toml || true)"
web_version_line="$(rg -n 'version-badge">v[0-9]+\.[0-9]+\.[0-9]+' web/index.html | head -n1 || true)"
if [[ -n "$pyproject_version" && -n "$web_version_line" ]]; then
  web_version="$(sed -E 's/.*v([0-9]+\.[0-9]+\.[0-9]+).*/\1/' <<<"$web_version_line")"
  if [[ "$pyproject_version" != "$web_version" ]]; then
    echo "Version drift detected: pyproject=$pyproject_version, web=$web_version"
    over=1
  else
    echo "Version badge matches pyproject: $pyproject_version"
  fi
fi

if (( strict == 1 && over == 1 )); then
  echo
  echo "Hygiene check failed (--strict)."
  exit 1
fi
