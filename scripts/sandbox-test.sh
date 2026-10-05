#!/usr/bin/env bash
# Runs the test suite where PyPI isn't reachable (Claude's sandbox), by
# cloning the pure-Python dependencies from GitHub at their pinned versions.
# With working pip, just `pip install -r requirements.txt -r requirements-dev.txt`
# and run `pytest`, or use `docker build --target test .` like CI.
#
# Usage: scripts/sandbox-test.sh [pytest args...]   (default: tests/)
# Clones once into $DEPS (default ${TMPDIR:-/tmp}/mkt-api-deps); re-runs reuse it.
# Packages already importable (and the compiled ones: pydantic, grpcio) aren't
# cloned. test_grpc.py is skipped: it needs compiled grpcio (the rest of the
# app imports grpc only inside its gRPC clients).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEPS=${DEPS:-${TMPDIR:-/tmp}/mkt-api-deps}
mkdir -p "$DEPS/stubs"

pin() {  # pin <package> -> its pinned version from requirements*.txt
  sed -n "s/^$1\(\[[a-z]*\]\)\?==\([0-9.]*\).*/\2/Ip" "$ROOT"/requirements*.txt | head -1
}

# import name | GitHub repo | tag (VER = pinned version) | path to add | default version
DEPS_TABLE="
fastapi|fastapi/fastapi|VER|.|$(pin fastapi)
httpx|encode/httpx|VER|.|$(pin httpx)
httpcore|encode/httpcore|VER|.|1.0.9
pytest|pytest-dev/pytest|VER|src|$(pin pytest)
pluggy|pytest-dev/pluggy|VER|src|1.6.0
iniconfig|pytest-dev/iniconfig|vVER|src|2.1.0
"
PYPATH="$DEPS/stubs"
while IFS='|' read -r mod repo tagfmt sub ver; do
  [ -n "$mod" ] || continue
  if python3 -c "import $mod" 2>/dev/null; then continue; fi
  tag=${tagfmt//VER/$ver}; [[ "$tagfmt" == rel_VER_ ]] && tag="rel_${ver//./_}"
  dir="$DEPS/${repo##*/}"
  if [ ! -d "$dir" ]; then
    echo "cloning $repo @ $tag" >&2
    git clone -q --depth 1 --branch "$tag" "https://github.com/$repo" "$dir" 2>/dev/null \
      || { echo "can't clone $repo at $tag; install $mod another way" >&2; exit 1; }
  fi
  # setuptools-scm packages need a _version.py that only a build writes.
  for pkg in "$dir/src/_pytest" "$dir/src/pluggy" "$dir/src/iniconfig"; do
    [ -d "$pkg" ] && [ ! -f "$pkg/_version.py" ] && printf '__version__ = version = "%s"\nversion_tuple = (%s)\n' "$ver" "${ver//./, }" > "$pkg/_version.py"
  done
  PYPATH="$PYPATH:$dir/$sub"
done <<< "$DEPS_TABLE"

# Stub: fastapi's annotated_doc marker.
mkdir -p "$DEPS/stubs/annotated_doc"
python3 -c "import annotated_doc" 2>/dev/null || printf 'class Doc:\n    def __init__(self, documentation):\n        self.documentation = documentation\n' > "$DEPS/stubs/annotated_doc/__init__.py"

cd "$ROOT"
[ $# -gt 0 ] || set -- tests/
PYTHONPATH="$PYPATH:${PYTHONPATH:-}" exec python3 -m pytest -q -p no:cacheprovider \
  --ignore=tests/test_grpc.py "$@"
