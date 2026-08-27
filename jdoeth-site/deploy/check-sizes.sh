#!/usr/bin/env bash
# What committing the games to git will actually cost you.
#
#   ./deploy/check-sizes.sh
#
# GitHub blocks any single file over 100 MB and warns over 50 MB. Repositories
# are fine into the hundreds of MB; you hear from support around a few GB.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GAMES="$HERE/web/content/games"

[ -d "$GAMES" ] || { echo "No games directory at $GAMES"; exit 1; }

echo "Per game:"
total=0
for d in "$GAMES"/*/; do
  [ -d "$d" ] || continue
  name=$(basename "$d")
  kb=$(du -sk "$d" | cut -f1)
  total=$((total + kb))
  printf "  %-24s %6s MB\n" "$name" "$((kb / 1024))"
done
echo "  ----"
printf "  %-24s %6s MB total\n" "" "$((total / 1024))"

echo
echo "Largest individual files:"
find "$GAMES" -type f -printf '%s\t%p\n' 2>/dev/null | sort -rn | head -5 | while IFS=$'\t' read -r bytes path; do
  mb=$((bytes / 1048576))
  flag=""
  [ "$mb" -ge 100 ] && flag="  <-- OVER GITHUB'S 100 MB HARD LIMIT"
  [ "$mb" -ge 50 ] && [ "$mb" -lt 100 ] && flag="  <-- over GitHub's 50 MB warning"
  printf "  %6s MB  %s%s\n" "$mb" "${path#$HERE/}" "$flag"
done

echo
if [ "$((total / 1024))" -lt 500 ]; then
  echo "Verdict: commit them normally. Well inside GitHub's limits, and these"
  echo "files never change, so there is no history to bloat."
else
  echo "Verdict: over 500 MB total. Consider a GitHub Release asset or Git LFS"
  echo "instead of committing directly."
fi
