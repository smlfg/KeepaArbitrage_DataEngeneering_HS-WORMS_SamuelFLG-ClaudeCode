#!/bin/bash
# Job 6: Code Quality Snapshot
# Zaehlt Lines of Code, Test Coverage, offene TODOs
# Einmal pro Nacht um Mitternacht — gut fuer die Dokumentation
PROJECT_DIR="/home/smlflg/DataEngeeneeringKEEPA/KeepaProjectsforDataEngeneering3BranchesMerge"
cd "$PROJECT_DIR" || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') === CODE QUALITY ==="

# Lines of Code
echo "--- Lines of Code (Python) ---"
find src/ -name "*.py" -not -path "*__pycache__*" | xargs wc -l 2>/dev/null | tail -1

echo "--- Lines of Tests ---"
find tests/ -name "*.py" -not -path "*__pycache__*" | xargs wc -l 2>/dev/null | tail -1

# File count
echo "--- Datei-Uebersicht ---"
echo "Python Source: $(find src/ -name '*.py' -not -path '*__pycache__*' | wc -l) Dateien"
echo "Python Tests:  $(find tests/ -name '*.py' -not -path '*__pycache__*' | wc -l) Dateien"
echo "Config Files:  $(find . -maxdepth 1 -name '*.yml' -o -name '*.yaml' -o -name '*.toml' -o -name '*.ini' | wc -l) Dateien"
echo "Docs:          $(find docs/ -name '*.md' 2>/dev/null | wc -l) Dateien"

# TODOs and FIXMEs
echo "--- Offene TODOs/FIXMEs ---"
grep -rn "TODO\|FIXME\|HACK\|XXX" src/ --include="*.py" 2>/dev/null | head -10
TODO_COUNT=$(grep -rc "TODO\|FIXME" src/ --include="*.py" 2>/dev/null | awk -F: '{sum+=$2} END{print sum}')
echo "Gesamt: ${TODO_COUNT:-0} TODOs/FIXMEs"

# Git status
echo "--- Git Status ---"
git status --short | head -10
echo "Uncommitted files: $(git status --short | wc -l)"

# Test run (quick, no actual API calls)
echo "--- Test Summary ---"
cd "$PROJECT_DIR"
python -m pytest tests/ --co -q 2>/dev/null | tail -3 || echo "Tests konnten nicht geladen werden"

echo ""
