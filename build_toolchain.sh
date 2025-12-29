#!/bin/bash
set -e  # Dừng ngay nếu gặp lỗi
set -u  # Báo lỗi nếu dùng biến chưa khai báo

# ==============================================================================
# 1. CẤU HÌNH (BẠN CÓ THỂ SỬA Ở ĐÂY)
# ==============================================================================

# Nếu biến môi trường BINUTILS_VERSION được set (từ Github Actions), dùng nó.
# Nếu không, dùng mặc định "2.42".
BINUTILS_VERSION="${BINUTILS_VERSION:-2.42}"
GCC_VERSION="${GCC_VERSION:-13.2.0}"

# Tương tự với Target
TARGET="${TARGET:-x86_64-elf}"

# Số luồng CPU để build (Lấy tối đa)
JOBS=$(nproc)

# Tên file kết quả
OUTPUT_PACKAGE="${TARGET}-gcc${GCC_VERSION}-binutils${BINUTILS_VERSION}.tar.gz"

# Đường dẫn làm việc
WORK_DIR="$(pwd)/toolchain_build"
SOURCES_DIR="$WORK_DIR/sources"
BUILD_DIR="$WORK_DIR/build"
INSTALL_DIR="$WORK_DIR/install" # Thư mục cài đặt tạm thời

# ==============================================================================
# 2. HÀM HỖ TRỢ (LOGGING & DOWNLOAD)
# ==============================================================================
info()  { printf "\033[0;32m[INFO]\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m[WARN]\033[0m %s\n" "$*"; }
error() { printf "\033[0;31m[ERROR]\033[0m %s\n" "$*" >&2; exit 1; }

download_and_extract() {
    local url=$1
    local file_name=$(basename "$url")
    local dir_name=$2

    mkdir -p "$SOURCES_DIR"
    cd "$SOURCES_DIR"

    if [ ! -d "$dir_name" ]; then
        if [ ! -f "$file_name" ]; then
            info "Downloading $file_name..."
            wget -q --show-progress "$url"
        else
            info "File $file_name already exists. Skipping download."
        fi
        
        info "Extracting $file_name..."
        tar -xf "$file_name"
    else
        info "Source $dir_name already extracted."
    fi
}

# ==============================================================================
# 3. QUÁ TRÌNH BUILD
# ==============================================================================

# Chuẩn bị thư mục
mkdir -p "$BUILD_DIR" "$INSTALL_DIR"
# Thêm bin vào PATH để GCC tìm thấy Binutils vừa build
export PATH="$INSTALL_DIR/bin:$PATH"

# --- BƯỚC 1: BINUTILS ---
info "=== STEP 1/3: BUILD BINUTILS $BINUTILS_VERSION ==="

download_and_extract \
    "https://ftp.gnu.org/gnu/binutils/binutils-$BINUTILS_VERSION.tar.xz" \
    "binutils-$BINUTILS_VERSION"

mkdir -p "$BUILD_DIR/binutils"
cd "$BUILD_DIR/binutils"

if [ ! -f Makefile ]; then
    info "Configuring Binutils..."
    "$SOURCES_DIR/binutils-$BINUTILS_VERSION/configure" \
        --target="$TARGET" \
        --prefix="$INSTALL_DIR" \
        --with-sysroot \
        --disable-nls \
        --disable-werror
fi

info "Compiling Binutils..."
make -j"$JOBS"
make install
info "Binutils installed to $INSTALL_DIR"

# --- BƯỚC 2: GCC ---
info "=== STEP 2/3: BUILD GCC $GCC_VERSION ==="

download_and_extract \
    "https://ftp.gnu.org/gnu/gcc/gcc-$GCC_VERSION/gcc-$GCC_VERSION.tar.xz" \
    "gcc-$GCC_VERSION"

# Tự động tải prerequisites (GMP, MPFR, MPC) vào trong source tree của GCC
if [ ! -d "$SOURCES_DIR/gcc-$GCC_VERSION/gmp" ]; then
    info "Downloading GCC prerequisites (gmp, mpfr, mpc)..."
    cd "$SOURCES_DIR/gcc-$GCC_VERSION"
    ./contrib/download_prerequisites
fi

mkdir -p "$BUILD_DIR/gcc"
cd "$BUILD_DIR/gcc"

if [ ! -f Makefile ]; then
    info "Configuring GCC..."
    # LƯU Ý: Đây là cấu hình cho OS Dev (Freestanding, no libc)
    "$SOURCES_DIR/gcc-$GCC_VERSION/configure" \
        --target="$TARGET" \
        --prefix="$INSTALL_DIR" \
        --disable-nls \
        --enable-languages=c,c++ \
        --without-headers \
        --disable-shared \
        --disable-multilib \
        --disable-threads \
        --disable-libgomp \
        --disable-libssp
fi

info "Compiling GCC (This may take a while)..."
make -j"$JOBS" all-gcc
make install-gcc

info "Compiling Libgcc..."
make -j"$JOBS" all-target-libgcc
make install-target-libgcc

# --- BƯỚC 3: ĐÓNG GÓI ---
info "=== STEP 3/3: PACKAGING ==="

cd "$WORK_DIR"
info "Creating archive: $OUTPUT_PACKAGE"

# Nén nội dung thư mục install (nhưng không lấy folder cha install/)
tar -czf "$OUTPUT_PACKAGE" -C "$INSTALL_DIR" .

info "SUCCESS!"
info "Toolchain Package: $WORK_DIR/$OUTPUT_PACKAGE"
info "Size: $(du -h "$OUTPUT_PACKAGE" | cut -f1)"