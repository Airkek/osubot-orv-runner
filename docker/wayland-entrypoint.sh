#!/bin/bash
set -euo pipefail

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/orv-wayland}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export SDL_VIDEO_DRIVER=wayland
export SDL_VIDEODRIVER=wayland
unset DISPLAY XAUTHORITY

mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
weston_log="$XDG_RUNTIME_DIR/weston.log"
egl_log="$XDG_RUNTIME_DIR/eglinfo.log"
weston_pid=
app_pid=

cleanup() {
    trap - EXIT TERM INT
    for pid in "$app_pid" "$weston_pid"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid" 2>/dev/null || true
            (
                sleep 10
                kill -KILL "$pid" 2>/dev/null || true
            ) &
            killer_pid=$!
            wait "$pid" 2>/dev/null || true
            kill "$killer_pid" 2>/dev/null || true
            wait "$killer_pid" 2>/dev/null || true
        fi
    done
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

weston --backend=headless --renderer=gl --shell=kiosk-shell.so \
    --socket="$WAYLAND_DISPLAY" --idle-time=0 --no-config \
    --width="${WESTON_WIDTH:-1920}" --height="${WESTON_HEIGHT:-1080}" \
    --log="$weston_log" &
weston_pid=$!

for ((attempt=0; attempt<300; attempt++)); do
    if ! kill -0 "$weston_pid" 2>/dev/null; then
        cat "$weston_log" >&2
        echo "Weston exited before its Wayland socket became ready" >&2
        exit 1
    fi
    if [ -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]; then
        break
    fi
    sleep 0.1
done
if [ ! -S "$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY" ]; then
    cat "$weston_log" >&2
    echo "Timed out waiting for Weston" >&2
    exit 1
fi

if ! timeout 30 eglinfo -B -p wayland > "$egl_log" 2>&1; then
    cat "$weston_log" "$egl_log" >&2
    echo "Wayland EGL initialization failed" >&2
    exit 1
fi
cat "$weston_log" "$egl_log"
if ! grep -Eq 'GL renderer: NVIDIA' "$weston_log" ||
    ! grep -Eq '^OpenGL .* vendor: NVIDIA' "$egl_log" ||
    grep -Eiq 'renderer:.*(llvmpipe|softpipe|swrast|software rasterizer)' "$weston_log" "$egl_log"; then
    echo "Hardware NVIDIA OpenGL is required; refusing software rendering" >&2
    exit 1
fi

"$@" &
app_pid=$!
set +e
wait -n "$weston_pid" "$app_pid"
result=$?
set -e
if ! kill -0 "$weston_pid" 2>/dev/null; then
    cat "$weston_log" >&2
    echo "Weston stopped while the runner was active" >&2
    exit 1
fi
exit "$result"
