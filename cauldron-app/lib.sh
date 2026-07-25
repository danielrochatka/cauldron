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
    cp "$example_file" "$config_file"
    first_run=1
  fi

  python3 - "$config_file" <<'PY'
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

  chmod 600 "$config_file"

  if [ "$first_run" -eq 1 ]; then
    echo "Created config.env with secure installation settings."
  fi
}
