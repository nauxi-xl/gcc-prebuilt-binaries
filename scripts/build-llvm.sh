#!/bin/bash
# scripts/build-llvm.sh

set -e

# Default values
TARGET="x86_64-elf"
PREFIX="${PWD}/install"
LLVM_VERSION="17.0.6"
BUILD_TYPE="Release"
JOBS=4
CMAKE_FLAGS=""

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
    --version)
      LLVM_VERSION="$2"
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
      CMAKE_FLAGS="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

echo "=== Building LLVM/Clang Toolchain ==="
echo "Target: $TARGET"
echo "Prefix: $PREFIX"
echo "LLVM version: $LLVM_VERSION"
echo "Build type: $BUILD_TYPE"
echo "Jobs: $JOBS"
echo "Additional CMake flags: $CMAKE_FLAGS"

# Create directories
mkdir -p "$PREFIX"
mkdir -p .cache/downloads
mkdir -p .cache/build

# Download LLVM
LLVM_MAJOR=$(echo $LLVM_VERSION | cut -d. -f1)
LLVM_URL="https://github.com/llvm/llvm-project/releases/download/llvmorg-$LLVM_VERSION/llvm-project-$LLVM_VERSION.src.tar.xz"

if [ ! -f ".cache/downloads/llvm-$LLVM_VERSION.tar.xz" ]; then
  echo "Downloading LLVM $LLVM_VERSION..."
  wget -q --show-progress -c "$LLVM_URL" -O ".cache/downloads/llvm-$LLVM_VERSION.tar.xz"
else
  echo "Using cached LLVM $LLVM_VERSION"
fi

if [ ! -d "llvm-project-$LLVM_VERSION.src" ]; then
  echo "Extracting LLVM $LLVM_VERSION..."
  tar -xf ".cache/downloads/llvm-$LLVM_VERSION.tar.xz"
fi

# Build LLVM
echo "Building LLVM/Clang..."
mkdir -p .cache/build/llvm && cd .cache/build/llvm

# Configure
cmake ../../llvm-project-$LLVM_VERSION.src/llvm \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_BUILD_TYPE="$BUILD_TYPE" \
  -DLLVM_ENABLE_PROJECTS="clang;lld" \
  -DLLVM_ENABLE_RUNTIMES="compiler-rt;libcxx;libcxxabi;libunwind" \
  -DLLVM_TARGETS_TO_BUILD="X86;AArch64;ARM;RISCV" \
  -DLLVM_DEFAULT_TARGET_TRIPLE="$TARGET" \
  -DLLVM_ENABLE_ASSERTIONS=OFF \
  -DLLVM_ENABLE_TERMINFO=OFF \
  -DLLVM_ENABLE_THREADS=ON \
  -DLLVM_ENABLE_LTO=OFF \
  -DLLVM_INCLUDE_EXAMPLES=OFF \
  -DLLVM_INCLUDE_TESTS=OFF \
  -DLLVM_INCLUDE_BENCHMARKS=OFF \
  -DLLVM_USE_LINKER=gold \
  -DCMAKE_CXX_FLAGS="-O3" \
  -G "Ninja" \
  $CMAKE_FLAGS

# Build and install
ninja -j$JOBS
ninja install

echo "=== LLVM/Clang toolchain built successfully ==="
echo "Toolchain installed to: $PREFIX"