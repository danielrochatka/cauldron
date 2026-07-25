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

# install_python_projects REPO_DIR REQUIREMENTS_FILE
#
# Installs requirements, the root cauldron package, and all Python packages
# under packages/. Skips directories without pyproject.toml (e.g. cauldron-astro).
install_python_projects() {
  local repo_dir="$1"
  local requirements_file="$2"

  pip install -q -r "$requirements_file"
  pip install -q -e "$repo_dir"

  for pkg in "$repo_dir"/packages/*; do
    [ -f "$pkg/pyproject.toml" ] || continue
    pip install -q -e "$pkg"
  done
}
