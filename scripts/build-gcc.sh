#!/bin/bash
# scripts/build-gcc.sh

set -e

# Default values
TARGET="x86_64-elf"
PREFIX="${PWD}/install"
GCC_VERSION="13.2.0"
BINUTILS_VERSION="2.42"
LANGUAGES="c,c++"
BUILD_TYPE="Release"
JOBS=4
CONFIGURE_FLAGS=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --target)
      TARGET="$2"
      shift 2
      ;;
    --prefix)
      PREFIX="$2"
      shift 2
      ;;
    --gcc-version)
      GCC_VERSION="$2"
      shift 2
      ;;
    --binutils-version)
      BINUTILS_VERSION="$2"
      shift 2
      ;;
    --languages)
      LANGUAGES="$2"
      shift 2
      ;;
    --build-type)
      BUILD_TYPE="$2"
      shift 2
      ;;
    --jobs)
      JOBS="$2"
      shift 2
      ;;
    --flags)
      CONFIGURE_FLAGS="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

echo "=== Building GCC/Binutils Toolchain ==="
echo "Target: $TARGET"
echo "Prefix: $PREFIX"
echo "GCC: $GCC_VERSION"
echo "Binutils: $BINUTILS_VERSION"
echo "Languages: $LANGUAGES"
echo "Build type: $BUILD_TYPE"
echo "Jobs: $JOBS"
echo "Additional flags: $CONFIGURE_FLAGS"

# Create directories
mkdir -p "$PREFIX"
mkdir -p .cache/downloads
mkdir -p .cache/build

download_source() {
  local name="$1"
  local version="$2"
  local url="$3"
  local file="$4"
  
  if [ ! -f ".cache/downloads/$file" ]; then
    echo "Downloading $name $version..."
    wget -q --show-progress -c "$url" -O ".cache/downloads/$file"
  else
    echo "Using cached $name $version"
  fi
  
  if [ ! -d "$name-$version" ]; then
    echo "Extracting $name $version..."
    tar -xf ".cache/downloads/$file"
  fi
}

# Download sources
download_source "binutils" "$BINUTILS_VERSION" \
  "https://ftp.gnu.org/gnu/binutils/binutils-$BINUTILS_VERSION.tar.xz" \
  "binutils-$BINUTILS_VERSION.tar.xz"

download_source "gcc" "$GCC_VERSION" \
  "https://ftp.gnu.org/gnu/gcc/gcc-$GCC_VERSION/gcc-$GCC_VERSION.tar.xz" \
  "gcc-$GCC_VERSION.tar.xz"

# Build binutils
echo "Building binutils..."
mkdir -p .cache/build/binutils && cd .cache/build/binutils

../../binutils-$BINUTILS_VERSION/configure \
  --target="$TARGET" \
  --prefix="$PREFIX" \
  --with-sysroot \
  --disable-nls \
  --disable-werror \
  --disable-multilib \
  $CONFIGURE_FLAGS

make -j$JOBS
make install
cd ../..

export PATH="$PREFIX/bin:$PATH"

# Build GCC
echo "Building GCC..."
mkdir -p .cache/build/gcc && cd .cache/build/gcc

# Download GCC prerequisites
cd ../../gcc-$GCC_VERSION
./contrib/download_prerequisites --no-verify
cd ../build/gcc

# Configure GCC
../../gcc-$GCC_VERSION/configure \
  --target="$TARGET" \
  --prefix="$PREFIX" \
  --disable-nls \
  --enable-languages="$LANGUAGES" \
  --without-headers \
  --with-gnu-as \
  --with-gnu-ld \
  --disable-libssp \
  --disable-libstdcxx-pch \
  --disable-multilib \
  $CONFIGURE_FLAGS

# Build GCC (minimal for cross-compiler)
make -j$JOBS all-gcc
make -j$JOBS all-target-libgcc
make install-gcc
make install-target-libgcc

echo "=== GCC/Binutils toolchain built successfully ==="
echo "Toolchain installed to: $PREFIX"