#!/usr/bin/env bash
# Cross-compile AgenEditor's minimal, static FFmpeg for Windows x64 inside the pinned Debian image.
# Inputs:  /src   verified source tarballs (scripts/build_ffmpeg.py checks every sha256 first)
#          /build this directory (build.sh, sources.json)
# Outputs: /out/bin/{ffmpeg,ffprobe}.exe, /out/configure.txt, /out/toolchain.txt
# Only what the engine uses is linked: x264 (H.264 encode), dav1d (AV1 decode), libass + freetype +
# fribidi + harfbuzz (caption burn-in), zlib. Everything else is FFmpeg's own native code.
set -euo pipefail

HOST=x86_64-w64-mingw32
P=/opt/prefix
W=/work
mkdir -p "$P" "$W" /out/bin

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  gcc-mingw-w64-x86-64-posix g++-mingw-w64-x86-64-posix binutils-mingw-w64-x86-64 mingw-w64-x86-64-dev \
  gcc libc6-dev make nasm meson ninja-build pkgconf python3 xz-utils bzip2 ca-certificates >/dev/null
update-alternatives --set "$HOST-gcc" "/usr/bin/$HOST-gcc-posix" >/dev/null
update-alternatives --set "$HOST-g++" "/usr/bin/$HOST-g++-posix" >/dev/null
dpkg-query -W -f '${Package} ${Version}\n' \
  gcc-mingw-w64-x86-64-posix g++-mingw-w64-x86-64-posix binutils-mingw-w64-x86-64 mingw-w64-x86-64-dev \
  gcc nasm meson ninja-build pkgconf >/out/toolchain.txt

export PKG_CONFIG_LIBDIR="$P/lib/pkgconfig"
export PKG_CONFIG_PATH=
cat >"$W/cross.ini" <<EOF
[binaries]
c = '$HOST-gcc'
cpp = '$HOST-g++'
ar = '$HOST-ar'
strip = '$HOST-strip'
windres = '$HOST-windres'
pkg-config = 'pkgconf'
nasm = 'nasm'

[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'

[properties]
pkg_config_libdir = ['$P/lib/pkgconfig']
EOF

quiet() { # run a step, showing its output only if it fails
  local log="$W/step.log"
  if ! "$@" >"$log" 2>&1; then
    tail -n 60 "$log" >&2
    return 1
  fi
}

unpack() { # name -> directory containing the unpacked source
  local file
  file=$(python3 -c "import json,sys;print(next(s['file'] for s in json.load(open('/build/sources.json'))['sources'] if s['name']==sys.argv[1]))" "$1")
  mkdir -p "$W/$1"
  tar -xf "/src/$file" -C "$W/$1" --strip-components=1
  echo "$W/$1"
}

meson_build() { # name [meson options...]
  local dir
  dir=$(unpack "$1")
  shift
  quiet meson setup "$dir/_build" "$dir" --cross-file "$W/cross.ini" --prefix "$P" --libdir lib \
    --default-library static --buildtype release --wrap-mode nodownload "$@"
  quiet ninja -C "$dir/_build" install
}

echo "== zlib"
dir=$(unpack zlib)
(cd "$dir" && CHOST=$HOST quiet ./configure --static --prefix="$P" && quiet make -j"$(nproc)" install)

echo "== freetype"
meson_build freetype -Dbrotli=disabled -Dbzip2=disabled -Dharfbuzz=disabled -Dpng=disabled -Dzlib=disabled

echo "== fribidi"
meson_build fribidi -Ddocs=false -Dbin=false -Dtests=false

echo "== harfbuzz"
meson_build harfbuzz -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dicu=disabled \
  -Dtests=disabled -Ddocs=disabled -Dutilities=disabled -Dintrospection=disabled

echo "== libass"
meson_build libass -Dfontconfig=disabled -Ddirectwrite=enabled -Dlibunibreak=disabled -Dtest=disabled \
  -Dcompare=disabled -Dprofile=disabled -Dfuzz=disabled -Dcheckasm=disabled

echo "== dav1d"
meson_build dav1d -Denable_tools=false -Denable_tests=false -Denable_examples=false

echo "== x264"
dir=$(unpack x264)
(cd "$dir" && quiet ./configure --host=$HOST --cross-prefix=$HOST- --prefix="$P" --enable-static --disable-cli \
  --disable-opencl && quiet make -j"$(nproc)" install)

echo "== ffmpeg"
dir=$(unpack ffmpeg)
cd "$dir"
./configure \
  --prefix="$P" --target-os=mingw32 --arch=x86_64 --cross-prefix=$HOST- --pkg-config=pkgconf \
  --pkg-config-flags=--static --extra-ldflags=-static --extra-libs="-lstdc++" \
  --enable-gpl --disable-autodetect --enable-w32threads \
  --disable-debug --disable-doc --disable-ffplay --disable-network \
  --enable-zlib --enable-libx264 --enable-libdav1d \
  --enable-libass --enable-libfreetype --enable-libfribidi --enable-libharfbuzz \
  >/out/configure.txt || { tail -n 40 ffbuild/config.log >&2; exit 1; }
quiet make -j"$(nproc)"
$HOST-strip -o /out/bin/ffmpeg.exe ffmpeg.exe
$HOST-strip -o /out/bin/ffprobe.exe ffprobe.exe
echo "== done"
