# README media rig: the pinned Ubuntu 26.04 image, apt snapshot and package set the
# GNOME Shell end-to-end test (tests/gnome_shell_bridge_e2e.js) runs in, plus what
# scripts/demo-capture.sh needs on top.
#
#   docker build -t snipsnap-demo-rig scripts/demo-rig
#   docker run -d --name snipsnap-demo --shm-size=2g -v <clone>:/work -v <out>:/out \
#       -v <dir with wallpaper.jpg>:/assets:ro snipsnap-demo-rig sleep infinity
#   docker exec snipsnap-demo bash -c 'cmake -S . -B build-gnome-e2e -G Ninja \
#       -DENABLE_GNOME_SHELL_BRIDGE=ON -DBUILD_TESTING=OFF -DDISABLE_UPDATE_CHECKER=ON \
#       -DENABLE_IMGUR=OFF && cmake --build build-gnome-e2e --target snipsnap'
#   docker exec snipsnap-demo scripts/demo-capture.sh
FROM docker.io/library/ubuntu@sha256:7c2884fd32770fc6c173b78e0dc2278a2851d89f5447919edbc45475ac55dd6a
ENV DEBIAN_FRONTEND=noninteractive
RUN set -eu; \
    test "$(dpkg --print-architecture)" = amd64; \
    e2e_sources=/etc/apt/sources.list.d/ubuntu.sources; \
    apt-get update -o APT::Update::Error-Mode=any; \
    apt-get install --yes --no-install-recommends ca-certificates=20260601~26.04.1; \
    sed -i '/^Signed-By: \/usr\/share\/keyrings\/ubuntu-archive-keyring.gpg$/a Snapshot: 20260718T000000Z' "$e2e_sources"; \
    test "$(grep -c '^Snapshot: 20260718T000000Z$' "$e2e_sources")" -eq 2; \
    find /var/lib/apt/lists -mindepth 1 -maxdepth 1 -delete; \
    apt-get update -o APT::Update::Error-Mode=any; \
    apt-get install --yes --no-install-recommends \
      ccache cmake dbus-daemon g++ git gnome-shell=50.1-0ubuntu1.1 libgl-dev \
      libkf6guiaddons-dev=6.24.0-0ubuntu1.1 \
      libmutter-test-18=50.1-0ubuntu2.2 make \
      mutter-18-tests=50.1-0ubuntu2.2 ninja-build \
      python3 python3-dbus python3-dbusmock python3-gi \
      qt6-base-dev=6.10.2+dfsg-7 qt6-l10n-tools qt6-svg-dev qt6-tools-dev \
      qt6-tools-dev-tools; \
    test "$(gnome-shell --version)" = "GNOME Shell 50.1"
# Demo extras: GTK apps for the desktop, Pillow for PNG stripping, and two Qt plugins the
# test image lacks: qt6-svg-plugins (without it every toolbar icon renders as an empty box;
# qt6-svg-dev does not pull it in) and qt6-gtk-platformtheme (the dark editor variant).
RUN set -eu; \
    apt-get install --yes --no-install-recommends \
      gnome-text-editor gnome-calculator adwaita-icon-theme fonts-cantarell fonts-noto-core \
      gsettings-desktop-schemas dconf-gsettings-backend python3-pil ffmpeg curl \
      qt6-svg-plugins qt6-gtk-platformtheme qt6-wayland; \
    mkdir -p /home/demo; \
    rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch 50.1 https://gitlab.gnome.org/GNOME/gnome-shell.git /opt/gnome-shell-50.1 \
    && test "$(git -C /opt/gnome-shell-50.1 rev-parse HEAD)" = e0fdc4c13250e9a9b8ea9594c83925274f4a5dca
ENV LIBGL_ALWAYS_SOFTWARE=1 \
    META_DBUS_RUNNER_DISABLE_LOGIND_PASSTHROUGH=1 \
    META_DBUS_RUNNER_DISABLE_UMOCKDEV=1 \
    GNOME_SHELL_SESSION_MODE=user \
    GNOME_SHELL_TEST_SOURCE=/opt/gnome-shell-50.1 \
    SNIPSNAP_REAL_PERF_HELPER=/usr/libexec/gnome-shell-perf-helper \
    GNOME_SHELL_BUILDDIR=/work/tests/gnome-shell-perf-helper \
    SNIPSNAP_TEST_EXECUTABLE=/work/build-gnome-e2e/src/snipsnap \
    CCACHE_DIR=/work/.ccache
WORKDIR /work
