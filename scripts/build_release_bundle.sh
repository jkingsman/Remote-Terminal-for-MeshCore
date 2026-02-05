#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/dist-bundle"
BUNDLE_DIR="${OUT_DIR}/remoteterm"

rm -rf "${OUT_DIR}"
mkdir -p "${BUNDLE_DIR}"

# Copy backend source
cp -R "${ROOT_DIR}/app" "${BUNDLE_DIR}/app"
cp "${ROOT_DIR}/pyproject.toml" "${BUNDLE_DIR}/pyproject.toml"
cp "${ROOT_DIR}/uv.lock" "${BUNDLE_DIR}/uv.lock"
cp "${ROOT_DIR}/README.md" "${BUNDLE_DIR}/README.md"

# Build frontend
pushd "${ROOT_DIR}/frontend" >/dev/null
npm ci
npm run build
popd >/dev/null

# Copy built frontend assets
mkdir -p "${BUNDLE_DIR}/frontend"
cp -R "${ROOT_DIR}/frontend/dist" "${BUNDLE_DIR}/frontend/dist"

# Create venv with dependencies (no dev)
pushd "${BUNDLE_DIR}" >/dev/null
uv venv .venv
. .venv/bin/activate
uv sync --frozen --no-dev

deactivate
popd >/dev/null

# Create run script
cat > "${BUNDLE_DIR}/run.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export PYTHONPATH="${ROOT_DIR}"

source "${ROOT_DIR}/.venv/bin/activate"

exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
EOF

chmod +x "${BUNDLE_DIR}/run.sh"

# Package
pushd "${OUT_DIR}" >/dev/null
tar -czf remoteterm-linux-x64.tar.gz remoteterm
popd >/dev/null

echo "Bundle created: ${OUT_DIR}/remoteterm-linux-x64.tar.gz"
