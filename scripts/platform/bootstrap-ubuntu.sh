#!/usr/bin/env bash
# Initial development tools only; this does not qualify the complete application.
set -euo pipefail

if [[ "$(id -u)" != 0 ]]; then
  echo "Run this provisioning script as Ubuntu's root account." >&2
  exit 2
fi
. /etc/os-release
if [[ "$ID" != ubuntu || "$VERSION_ID" != 24.04 ]]; then
  echo "This script is qualified for Ubuntu 24.04 only. Review it for another release." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git build-essential \
  python3 python3-venv python3-pip bubblewrap xz-utils

# A fixed official Linux Node.js archive avoids accidentally using Windows npm.
# Update this version deliberately, together with the compatibility documentation.
node_version=24.22.2
if [[ "$(uname -m)" != x86_64 ]]; then
  echo "The selected Node.js archive requires x86_64. Review for another architecture." >&2
  exit 2
fi
archive="node-v${node_version}-linux-x64.tar.xz"
install_root=/opt/ev-dt
node_root="${install_root}/node-v${node_version}-linux-x64"

if [[ ! -d "$node_root" ]]; then
  download_dir="$(mktemp -d /tmp/ev-dt-node.XXXXXXXX)"
  cleanup_download() {
    rm -f -- "$download_dir/$archive" "$download_dir/SHASUMS256.txt"
    rmdir -- "$download_dir"
  }
  trap cleanup_download EXIT
  curl --fail --location --show-error --silent --retry 2 --connect-timeout 20 --max-time 300 \
    "https://nodejs.org/dist/v${node_version}/${archive}" -o "$download_dir/$archive"
  curl --fail --location --show-error --silent --retry 2 --connect-timeout 20 --max-time 60 \
    "https://nodejs.org/dist/v${node_version}/SHASUMS256.txt" -o "$download_dir/SHASUMS256.txt"
  (
    cd "$download_dir"
    awk -v file="$archive" '$2 == file { print; found=1 } END { if (!found) exit 1 }' SHASUMS256.txt |
      sha256sum --check --strict -
  )
  install -d -m 0755 "$install_root"
  tar --extract --xz --file "$download_dir/$archive" --directory "$install_root" --no-same-owner
fi

test "$("$node_root/bin/node" --version)" = "v${node_version}"
for tool in node npm npx; do
  target="/usr/local/bin/$tool"
  # Avoid overwriting an unrelated pre-existing Linux installation.
  if [[ -e "$target" || -L "$target" ]]; then
    if [[ "$(readlink "$target" || true)" != "$node_root/bin/$tool" ]]; then
      echo "Refusing to overwrite an existing command: $target" >&2
      exit 2
    fi
  else
    ln -s "$node_root/bin/$tool" "$target"
  fi
done
node --version
npm --version
printf '\nBase tooling installed. Account, access, and sandbox checks still need verification.\n'
