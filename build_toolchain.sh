#!/bin/bash

# ==============================================================================
# CONFIGURATION & DEFAULTS
# ==============================================================================
set -e # Dừng ngay nếu có lỗi
set -u # Báo lỗi nếu dùng biến chưa khai báo

# Đường dẫn gốc (Fix lỗi cú pháp $(dir $0))
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$ROOT_DIR/sources"
BUILD_ROOT="$ROOT_DIR/build"
INSTALL_PREFIX="$ROOT_DIR/opt/toolchain"

GCC_VERSION="15.2.0"
BINUTILS_VERSION="2.42"
LLVM_VERSION="21.0.8"
RUST_VERSION="1.91.0"
GLIBC_VERSION="2.40"
NEWLIB_VERSION="4.4.0.20231231"

# Mặc định
BUILD_MODE="native"   # native | cross
TARGET_ARCH=""        # VD: x86_64-elf, aarch64-linux-gnu
ENABLE_LIBC=""        # glibc | newlib | musl | none
TOOLCHAINS=""         # Danh sách toolchain cần build (gcc,llvm,rust)
LLVM_PROJECTS="clang;lld;compiler-rt;libcxx;libcxxabi" # Mặc định cho LLVM
JOBS=$(nproc)         # Số luồng CPU

DEBUG_MODE=1

# ==============================================================================
# LOGGING UTILS
# ==============================================================================
get_time() { date "+%H:%M:%S"; }

log() {
    local level=$1
    local color=$2
    shift 2
    local message="$*"
    printf "%s [%b%-5s%b] %s\n" "$(get_time)" "$color" "$level" "\033[0m" "$message"
}

info()  { log "INFO"  "\033[0;32m" "$@"; }
warn()  { log "WARN"  "\033[1;33m" "$@"; }
error() { log "ERROR" "\033[0;31m" "$@" >&2; }
debug() { [ "$DEBUG_MODE" -eq 1 ] && log "DEBUG" "\033[0;34m" "$@"; }

# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================
usage() {
    cat <<EOF
Usage: $0 [options] ...

Options:
    --toolchain=<list>      Comma separated list: gcc,llvm,rust,binutils (e.g., "gcc,llvm")
    --target=<triple>       Target triple (e.g., x86_64-elf). If empty, builds Native.
    --prefix=<path>         Install directory (default: $INSTALL_PREFIX)
    --jobs=<n>              Number of parallel jobs (default: $JOBS)
    
    --enable-libc=<name>    glibc, newlib, musl.
    
    LLVM Specific:
    --llvm-projects=<list>  Semicolon separated list (clang;lld;polly...)
    
    Versions:
    --gcc-ver=<ver>         Default: $GCC_VERSION
    --llvm-ver=<ver>        Default: $LLVM_VERSION
    
EOF
    exit 1
}

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --help) usage ;;
        --toolchain=*) TOOLCHAINS="${1#*=}" ;;
        --target=*)    TARGET_ARCH="${1#*=}" ;;
        --prefix=*)    INSTALL_PREFIX="${1#*=}" ;;
        --source-dir=*) SOURCE_DIR="${1#*=}" ;;
        --jobs=*)      JOBS="${1#*=}" ;;
        
        --enable-libc=*) ENABLE_LIBC="${1#*=}" ;;
        
        --llvm-projects=*) LLVM_PROJECTS="${1#*=}" ;;
        
        --gcc-ver=*)   GCC_VERSION="${1#*=}" ;;
        --llvm-ver=*)  LLVM_VERSION="${1#*=}" ;;
        
        *) error "Unknown option: $1"; usage ;;
    esac
    shift
done

# ==============================================================================
# PREPARATION & LOGIC
# ==============================================================================

download_and_extract() {
    local url="$1"
    local archive_name="$2"
    local extract_dir_name="$3" # Tên thư mục sau khi giải nén (để check tồn tại)

    mkdir -p "$SOURCE_DIR"
    cd "$SOURCE_DIR"

    # Nếu thư mục source chưa tồn tại thì mới tải
    if [ ! -d "$extract_dir_name" ]; then
        info "Source '$extract_dir_name' not found. Downloading..."
        
        # Tải file nén nếu chưa có
        if [ ! -f "$archive_name" ]; then
            wget -q --show-progress "$url" -O "$archive_name"
        fi
        
        info "Extracting $archive_name..."
        tar -xf "$archive_name"
        
        # Xử lý trường hợp tên thư mục giải nén khác với tên mong đợi (đặc biệt là LLVM)
        # Ví dụ: llvm giải nén ra "llvm-project-17.0.6.src", ta muốn đổi thành "llvm-project"
        if [ "$extract_dir_name" == "llvm-project" ]; then
             # Tìm thư mục vừa giải nén có chữ llvm
             local actual_dir=$(find . -maxdepth 1 -type d -name "llvm-project*src" | head -n 1)
             if [ -n "$actual_dir" ]; then
                mv "$actual_dir" llvm-project
             fi
        fi
    else
        info "Source '$extract_dir_name' found. Skipping download."
    fi
}

prepare_sources() {
    local tool="$1"
    case $tool in
        binutils)
            download_and_extract \
                "https://ftp.gnu.org/gnu/binutils/binutils-$BINUTILS_VERSION.tar.xz" \
                "binutils-$BINUTILS_VERSION.tar.xz" \
                "binutils-$BINUTILS_VERSION"
            ;;
        gcc)
            download_and_extract \
                "https://ftp.gnu.org/gnu/gcc/gcc-$GCC_VERSION/gcc-$GCC_VERSION.tar.xz" \
                "gcc-$GCC_VERSION.tar.xz" \
                "gcc-$GCC_VERSION"
                
            # GCC cần tải thêm các prerequisites (gmp, mpfr...)
            # Chỉ chạy nếu chưa có thư mục gmp bên trong
            if [ -d "gcc-$GCC_VERSION" ] && [ ! -d "gcc-$GCC_VERSION/gmp" ]; then
                info "Downloading GCC prerequisites..."
                cd "gcc-$GCC_VERSION"
                ./contrib/download_prerequisites
                cd ..
            fi
            ;;
        llvm)
            # URL GitHub Release của LLVM
            download_and_extract \
                "https://github.com/llvm/llvm-project/releases/download/llvmorg-$LLVM_VERSION/llvm-project-$LLVM_VERSION.src.tar.xz" \
                "llvm-project-$LLVM_VERSION.src.tar.xz" \
                "llvm-project"
            ;;
    esac
}

# Xác định chế độ Build (Native hay Cross)
HOST_MACH=$(uname -m)-linux-gnu
if [[ -z "$TARGET_ARCH" ]]; then
    info "No target specified. Building NATIVE toolchain for host ($HOST_MACH)."
    TARGET_ARCH=$HOST_MACH
    BUILD_MODE="native"
else
    info "Target specified: $TARGET_ARCH. Building CROSS toolchain."
    BUILD_MODE="cross"
fi

mkdir -p "$SOURCE_DIR" "$BUILD_ROOT" "$INSTALL_PREFIX"
export PATH="$INSTALL_PREFIX/bin:$PATH" # Để GCC stage 2 tìm thấy binutils

# ==============================================================================
# BUILDERS
# ==============================================================================

build_binutils() {
    prepare_sources "binutils"

    info "Building Binutils $BINUTILS_VERSION for $TARGET_ARCH..."
    local bdir="$BUILD_ROOT/binutils-$TARGET_ARCH"
    mkdir -p "$bdir" && cd "$bdir"
    
    # Download source logic here (omitted for brevity) - giả sử source đã có
    # Check if configured
    if [ ! -f Makefile ]; then
        "$SOURCE_DIR/binutils-$BINUTILS_VERSION/configure" \
            --target="$TARGET_ARCH" \
            --prefix="$INSTALL_PREFIX" \
            --with-sysroot="$INSTALL_PREFIX/$TARGET_ARCH/sysroot" \
            --disable-nls \
            --disable-werror \
            --enable-plugins \
            --enable-gold=yes
    fi
    
    make -j"$JOBS"
    make install
    info "Binutils installed."
}

build_gcc() {
    prepare_sources "gcc"

    info "Building GCC $GCC_VERSION ($BUILD_MODE)..."
    local bdir="$BUILD_ROOT/gcc-$TARGET_ARCH"
    mkdir -p "$bdir" && cd "$bdir"

    # Common Configs
    local conf_opts=(
        "--target=$TARGET_ARCH"
        "--prefix=$INSTALL_PREFIX"
        "--disable-nls"
        "--enable-languages=c,c++"
        "--disable-multilib"
    )

    if [[ "$BUILD_MODE" == "cross" ]]; then
        # Cross-Compiler Config
        if [[ -z "$ENABLE_LIBC" ]]; then
            # Stage 1 (Freestanding / Kernel Dev)
            conf_opts+=( "--without-headers" "--with-newlib" "--disable-shared" "--disable-threads" "--disable-libssp" )
        else
            # Stage 2 (With Libc)
            conf_opts+=( "--with-sysroot=$INSTALL_PREFIX/$TARGET_ARCH/sysroot" )
        fi
    else
        # Native Config (Build compiler CHẠY TRÊN máy này)
        conf_opts+=( "--enable-shared" "--enable-threads=posix" "--with-system-zlib" )
    fi

    # Prerequisites (GMP/MPFR/MPC) handled inside GCC source usually via ./contrib/download_prerequisites
    
    if [ ! -f Makefile ]; then
        "$SOURCE_DIR/gcc-$GCC_VERSION/configure" "${conf_opts[@]}"
    fi

    make -j"$JOBS" all-gcc
    make install-gcc
    
    # Nếu không phải freestanding mode thì build tiếp libgcc
    if [[ "$BUILD_MODE" == "native" ]] || [[ -n "$ENABLE_LIBC" ]]; then
         make -j"$JOBS" all-target-libgcc
         make install-target-libgcc
    fi
    info "GCC installed."
}

build_llvm() {
    prepare_sources "llvm"
    info "Building LLVM $LLVM_VERSION ($BUILD_MODE)..."
    local bdir="$BUILD_ROOT/llvm-$TARGET_ARCH"
    mkdir -p "$bdir" && cd "$bdir"

    # CMake Flags cơ bản
    local cmake_flags=(
        "-G Ninja"
        "-DCMAKE_BUILD_TYPE=Release"
        "-DCMAKE_INSTALL_PREFIX=$INSTALL_PREFIX"
        "-DLLVM_ENABLE_PROJECTS=$LLVM_PROJECTS"
        "-DLLVM_ENABLE_RUNTIMES=libcxx;libcxxabi;libunwind" # Optional, tùy user
        "-DLLVM_TARGETS_TO_BUILD=X86;ARM;AArch64" # Tùy chọn, build hết thì lâu
    )

    if [[ "$BUILD_MODE" == "cross" ]]; then
        # Cấu hình Cross LLVM:
        # LLVM bản chất là Cross-compiler sẵn. Nhưng nếu muốn libs (libc++, compiler-rt)
        # chạy trên Target, ta phải set LLVM_DEFAULT_TARGET_TRIPLE.
        cmake_flags+=(
            "-DLLVM_DEFAULT_TARGET_TRIPLE=$TARGET_ARCH"
            # Nếu build cho bare-metal
            "-DLLVM_ENABLE_PER_TARGET_RUNTIME_DIR=ON" 
        )
    fi

    cmake "$SOURCE_DIR/llvm-project" "${cmake_flags[@]}"
    ninja -j"$JOBS"
    ninja install
    info "LLVM installed."
}

build_rust() {
    info "Building Rust $RUST_VERSION..."
    # Rust cần file config.toml phức tạp.
    # Logic build Rust từ source thường gọi ./x.py
    
    cd "$SOURCE_DIR/rust"
    
    # Tạo config.toml động
    cat <<EOF > config.toml
[build]
target = ["$TARGET_ARCH"]
[install]
prefix = "$INSTALL_PREFIX"
[rust]
channel = "stable"
EOF

    ./x.py build -j "$JOBS"
    ./x.py install
    info "Rust installed."
}

# ==============================================================================
# MAIN EXECUTION LOOP
# ==============================================================================

# Tách chuỗi toolchain (vd: "gcc,llvm" -> mảng)
IFS=',' read -ra ADDR <<< "$TOOLCHAINS"

for tool in "${ADDR[@]}"; do
    case $tool in
        binutils) build_binutils ;;
        gcc)      
            # Thường phải build binutils trước GCC
            build_binutils
            build_gcc 
            ;;
        llvm)     build_llvm ;;
        rust)     build_rust ;;
        *)        warn "Skipping unknown toolchain: $tool" ;;
    esac
done

info "All requested toolchains built successfully!"
info "Toolchain location: $INSTALL_PREFIX"