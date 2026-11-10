#!/usr/bin/env bash
# Install for the desktop user. sudo is used only for system packages.
set -Eeuo pipefail

fail() { printf '\nVideo Watcher: %s\n' "$*" >&2; exit 1; }

download() {
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --silent --show-error --retry 2 "$1" --output "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget --quiet --output-document="$2" "$1"
    else
        fail 'Please install curl or wget, then run this command again.'
    fi
}

install_dependencies() {
    # shellcheck disable=SC1091
    source /etc/os-release
    local family=" ${ID:-} ${ID_LIKE:-} "
    local -a packages
    [[ ! -e /run/ostree-booted ]] || fail 'Atomic desktops need a manual install; see docs/DETAILS.md.'
    command -v sudo >/dev/null || fail 'sudo is required to install system packages. Ask your administrator to install the dependencies in docs/DETAILS.md.'
    case "$family" in
        *' debian '*|*' ubuntu '*|*' linuxmint '*)
            packages=(python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 vlc vlc-plugin-base)
            printf '\nInstalling Debian/Ubuntu packages (your password may be requested)...\n'
            sudo apt-get update
            sudo apt-get install -y "${packages[@]}"
            ;;
        *' arch '*)
            packages=(python python-gobject gtk4 libadwaita vlc vlc-plugin-dbus vlc-plugin-ffmpeg)
            printf '\nInstalling Arch packages (your password may be requested)...\n'
            sudo pacman -S --needed --noconfirm "${packages[@]}"
            ;;
        *' fedora '*)
            packages=(python3 python3-gobject gtk4 libadwaita vlc vlc-plugin-ffmpeg)
            printf '\nInstalling Fedora packages (your password may be requested)...\n'
            sudo dnf install -y "${packages[@]}"
            ;;
        *) fail "No automatic package setup for ${PRETTY_NAME:-this distribution}. See docs/DETAILS.md for a manual install." ;;
    esac
}

main() {
    local source_dir='' prefix='' skip_deps=false autostart=true
    local release='main' workdir
    local -a install_args=()
    while (($#)); do
        case "$1" in
            --no-autostart) autostart=false; shift ;;
            --skip-deps) skip_deps=true; shift ;;
            --source|--prefix)
                (($# >= 2)) || fail "$1 needs a directory."
                if [[ "$1" == --source ]]; then source_dir=$2; else prefix=$2; fi
                shift 2 ;;
            --help|-h)
                printf 'Usage: bash install.sh [--no-autostart] [--skip-deps] [--source DIR] [--prefix DIR]\n'
                return ;;
            *) fail "Unknown option: $1" ;;
        esac
    done
    [[ $(uname -s) == Linux ]] || fail 'This app is for Linux desktops.'
    if ((EUID == 0)) && [[ -z "$prefix" ]]; then
        fail 'Run this as your normal desktop user, without sudo. The installer will ask for your password when needed.'
    fi
    printf '\nVideo Watcher — let the cat remember which lesson you watched.\n'
    if [[ -z "$source_dir" ]]; then
        workdir=$(mktemp -d -t videowatcher-install.XXXXXXXX)
        # Expand only our freshly-created temporary path, safely quoted by printf.
        # shellcheck disable=SC2064
        trap "rm -rf -- $(printf '%q' "$workdir")" EXIT
        printf 'Downloading Video Watcher %s...\n' "$release"
        download "https://github.com/mathisen99/videowatcher/archive/refs/heads/${release}.tar.gz" "$workdir/source.tar.gz"
        tar -xzf "$workdir/source.tar.gz" -C "$workdir"
        source_dir="$workdir/videowatcher-${release}"
    fi
    [[ -f "$source_dir/install.py" && -f "$source_dir/videowatcher/__main__.py" ]] || fail 'The app download is incomplete.'
    if ! "$skip_deps"; then install_dependencies; fi
    [[ -x /usr/bin/python3 ]] || fail 'System Python 3 is missing.'
    /usr/bin/python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit('Video Watcher needs Python 3.10 or newer.')
try:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Gtk, Adw
    if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 6):
        raise ValueError('GTK 4.6 or newer is required.')
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 1):
        raise ValueError('libadwaita 1.1 or newer is required.')
except (ImportError, ValueError) as error:
    raise SystemExit(f'Missing desktop dependencies: {error}')
PY
    if "$autostart"; then install_args+=(--autostart); fi
    if [[ -n "$prefix" ]]; then install_args+=(--prefix "$prefix"); fi
    /usr/bin/python3 "$source_dir/install.py" "${install_args[@]}"
    cat <<'CAT'

       /\_/\
      ( o.o )  Installation complete. I did all the work.
       > ^ <   You may now return to your regularly scheduled procrastination.
      /|   |\
     (_|   |_)

  Open "Video Watcher" from your app menu and add a course folder.
  If an older version is open, quit it before reopening the updated app.
  Your videos and watch history stay right where they are. Purrfect.

CAT
}

# Execution at the end also avoids running a partially downloaded script.
main "$@"
