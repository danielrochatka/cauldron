# Cauldron shared shell functions — sourced by start, stop, update, manage.
# Do not execute this file directly.

# initialize_config CONFIG_FILE EXAMPLE_FILE
#
# Ensures config.env exists and contains a stable SECRET_KEY.
#   • Creates config.env from config.env.example when absent.
#   • Generates a key when SECRET_KEY is blank or missing.
#   • Never overwrites an existing non-empty key.
#   • Writes atomically (tmp → replace) with permissions 600.
#   • Never prints the generated key.
initialize_config() {
  local config_file="$1"
  local example_file="$2"
  local first_run=0

  if [ ! -f "$config_file" ]; then
    if [ ! -f "$example_file" ]; then
      echo "ERROR: Configuration template not found: $example_file" >&2
      return 1
    fi
    if ! cp "$example_file" "$config_file"; then
      echo "ERROR: Failed to create $config_file from template." >&2
      return 1
    fi
    first_run=1
  fi

  if ! python3 - "$config_file" <<'PY'
from pathlib import Path
import os, secrets, sys, tempfile

path = Path(sys.argv[1])
text = path.read_text()

lines = text.splitlines()
found = False
updated = []

for line in lines:
    if line.startswith("SECRET_KEY="):
        value = line.partition("=")[2].strip()
        if value:
            found = True
            updated.append(line)
        else:
            updated.append(f"SECRET_KEY={secrets.token_hex(32)}")
            found = True
    else:
        updated.append(line)

if not found:
    updated.append(f"SECRET_KEY={secrets.token_hex(32)}")

new_text = "\n".join(updated) + "\n"

fd, tmp = tempfile.mkstemp(prefix=".config.env.", dir=path.parent, text=True)
try:
    with os.fdopen(fd, "w") as f:
        f.write(new_text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
finally:
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
PY
  then
    echo "ERROR: Failed to write configuration to $config_file." >&2
    return 1
  fi

  if ! chmod 600 "$config_file"; then
    echo "ERROR: Failed to set permissions on $config_file." >&2
    return 1
  fi

  if [ "$first_run" -eq 1 ]; then
    echo "Created config.env with secure installation settings."
  fi
}

# check_node_version MIN_MAJOR
#
# Verifies that node is in PATH and that its major version is >= MIN_MAJOR.
# Prints a diagnostic line on success ("--> Node.js vX.Y.Z OK").
# Prints an error to stderr and returns 1 on failure.
check_node_version() {
  local min_major="${1:-18}"

  if ! command -v node &>/dev/null; then
    echo "ERROR: Node.js is required but was not found in PATH." >&2
    echo "       Install Node.js ${min_major} or newer from https://nodejs.org/" >&2
    return 1
  fi

  local raw
  if ! raw=$(node --version 2>/dev/null); then
    echo "ERROR: 'node --version' failed. Ensure Node.js is properly installed." >&2
    return 1
  fi

  local major
  major=$(echo "$raw" | sed -n 's/^v\([0-9]*\)\..*/\1/p')
  if [ -z "$major" ]; then
    echo "ERROR: Could not parse Node.js version from: '$raw'" >&2
    echo "       Install Node.js ${min_major} or newer from https://nodejs.org/" >&2
    return 1
  fi

  if [ "$major" -lt "$min_major" ]; then
    echo "ERROR: Node.js ${min_major} or newer is required. Found: $raw" >&2
    echo "       Install Node.js ${min_major} or newer from https://nodejs.org/" >&2
    return 1
  fi

  echo "--> Node.js $raw OK"
}

# _kill_port PORT
#
# Kill any process listening on PORT so the server can bind cleanly.
# Used by launch_server before starting a new process.
_kill_port() {
  local port="$1"
  local pid
  # ss is available on all modern Linux; fuser is a portable fallback.
  if command -v ss &>/dev/null; then
    pid=$(ss -tlnp "sport = :$port" 2>/dev/null \
      | grep -oP '(?<=pid=)\d+' | head -1) || true
  elif command -v fuser &>/dev/null; then
    pid=$(fuser "${port}/tcp" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' | head -1) || true
  fi
  if [ -n "${pid:-}" ]; then
    echo "    Stopping existing process on port $port (pid $pid)..."
    kill -TERM "$pid" 2>/dev/null || true
    local waited=0
    while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 15 ]; do
      sleep 1; waited=$((waited + 1))
    done
    kill -KILL "$pid" 2>/dev/null || true
  fi
}

# launch_server CAULDRON_DIR PORT
#
# Starts Gunicorn in daemon mode, or falls back to Django's dev server.
# Called from ./start and ./update so each performs this step exactly once.
#
# Always kills whatever is on PORT first so a stale server from a previous
# run (especially a dev-mode runserver without a PID file) does not shadow
# the new process or fool the health check.
launch_server() {
  local cauldron_dir="$1"
  local port="$2"
  local pid_file="$cauldron_dir/data/cauldron.pid"

  _kill_port "$port"

  if command -v gunicorn &>/dev/null; then
    # Re-source config.env so SECRET_KEY and other env vars are guaranteed
    # available to gunicorn workers regardless of whether the caller already
    # exported them.  This makes launch_server self-contained and prevents the
    # "SECRET_KEY is not set" crash that occurs when variables exported early
    # in ./update are lost before the server starts (e.g. after re-sourcing
    # lib.sh between the export and the gunicorn call).
    if [ -f "$cauldron_dir/config.env" ]; then
      set -a
      # shellcheck source=/dev/null
      source "$cauldron_dir/config.env"
      set +a
    fi

    gunicorn \
      --chdir "$cauldron_dir" \
      --bind "${CAULDRON_HOST:-0.0.0.0}:$port" \
      --workers "${CAULDRON_WORKERS:-2}" \
      --timeout "${CAULDRON_TIMEOUT:-300}" \
      --pid "$pid_file" \
      --access-logfile "$cauldron_dir/logs/access.log" \
      --error-logfile "$cauldron_dir/logs/error.log" \
      --daemon \
      cauldron_site.wsgi:application

    # Poll for the PID file: gunicorn writes it when the master starts.
    # A missing PID file means the master crashed before forking workers,
    # which the HTTP health check would never catch (no process to respond).
    local waited=0
    while [ ! -f "$pid_file" ] && [ "$waited" -lt 5 ]; do
      sleep 1
      waited=$((waited + 1))
    done

    if [ ! -f "$pid_file" ]; then
      echo "ERROR: Gunicorn master did not write a PID file within ${waited}s." >&2
      echo "       The master process may have crashed immediately." >&2
      echo "       Last lines of error log:" >&2
      tail -10 "$cauldron_dir/logs/error.log" 2>/dev/null | sed 's/^/         /' >&2 || true
      return 1
    fi

    echo "Cauldron started on http://localhost:$port (gunicorn, pid $(cat "$pid_file"))"
  else
    # Dev-server fallback: background it, write our own PID file so ./stop and
    # future ./update runs can terminate it cleanly.
    echo "Cauldron starting on http://localhost:$port (dev server — logs to stderr)"
    "$cauldron_dir/manage" runserver "0.0.0.0:$port" &
    local dev_pid=$!
    echo "$dev_pid" > "$pid_file"
    # Disown so the process survives the shell exiting (./start use case).
    disown "$dev_pid" 2>/dev/null || true
  fi
}

# install_python_projects REPO_DIR REQUIREMENTS_FILE
#
# Installs requirements, the root cauldron package, and all Python packages
# under packages/ in a single pip invocation so pip can resolve cross-package
# dependencies correctly. Skips non-Python dirs (e.g. cauldron-astro).
install_python_projects() {
  local repo_dir="$1"
  local requirements_file="$2"

  local args=(-q -r "$requirements_file" -e "$repo_dir")
  for pkg in "$repo_dir"/packages/*; do
    [ -f "$pkg/pyproject.toml" ] || continue
    args+=(-e "$pkg")
  done

  pip install "${args[@]}"
}

# install_frontend CAULDRON_DIR
#
# Runs `npm ci` in frontend/ using the tracked package-lock.json, verifies the
# local Astro binary is present and executable, and prints the Astro version.
# Fails with a clear error message when npm or Astro installation fails.
# No-ops when frontend/package.json is absent (no frontend configured).
install_frontend() {
  local cauldron_dir="$1"
  local frontend_dir="$cauldron_dir/frontend"

  if [ ! -f "$frontend_dir/package.json" ]; then
    return 0  # No frontend configured; skip silently.
  fi

  echo "--> Installing frontend dependencies..."
  if ! npm ci --prefix "$frontend_dir"; then
    echo "ERROR: npm install failed. Ensure Node.js and npm are installed," >&2
    echo "       then run: ./install" >&2
    return 1
  fi

  local astro_bin="$frontend_dir/node_modules/.bin/astro"
  if [ ! -x "$astro_bin" ]; then
    echo "ERROR: Astro binary not found after npm install: $astro_bin" >&2
    echo "       Run: ./install" >&2
    return 1
  fi

  local astro_version
  if ! astro_version=$("$astro_bin" --version 2>&1); then
    echo "ERROR: Astro binary failed to run (exit $?)." >&2
    echo "       This usually means the installed Node.js version is incompatible." >&2
    echo "       Output: $astro_version" >&2
    echo "       Run: ./install" >&2
    return 1
  fi
  echo "--> Astro ${astro_version} installed."
}

# is_frontend_installed CAULDRON_DIR
#
# Returns 0 (true) when the local Astro binary is present and executable.
is_frontend_installed() {
  local cauldron_dir="$1"
  [ -x "$cauldron_dir/frontend/node_modules/.bin/astro" ]
}

# is_installation_ready CAULDRON_DIR
#
# Returns 0 when all installation artifacts are present:
#   - .venv/bin/python (Python virtualenv)
#   - config.env (configuration file)
#   - frontend/node_modules/.bin/astro (when frontend/package.json exists)
# Prints a diagnostic line to stderr for each missing artifact and returns 1
# when anything is absent.
is_installation_ready() {
  local cauldron_dir="$1"
  local ok=1

  if [ ! -x "$cauldron_dir/.venv/bin/python" ]; then
    echo "  Missing: .venv/bin/python (Python virtualenv not installed)" >&2
    ok=0
  fi

  if [ ! -f "$cauldron_dir/config.env" ]; then
    echo "  Missing: config.env (configuration not initialised)" >&2
    ok=0
  fi

  if [ -f "$cauldron_dir/frontend/package.json" ] && \
     [ ! -x "$cauldron_dir/frontend/node_modules/.bin/astro" ]; then
    echo "  Missing: frontend/node_modules/.bin/astro (frontend not installed)" >&2
    ok=0
  fi

  return $((1 - ok))
}
