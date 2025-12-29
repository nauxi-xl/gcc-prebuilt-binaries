#!/usr/bin/env python3
"""
🏗️ Cross Compiler Builder Pro
Toolchain builder with support for GCC/Binutils, LLVM, C libraries, and GitHub Actions integration.
"""

import argparse
import json
import os
import sys
import subprocess
import shutil
import tarfile
import zipfile
import urllib.request
import hashlib
import tempfile
import shlex
from pathlib import Path
from typing import Dict, List, Optional, Union, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import yaml

# ============================================================================
# ENUMS AND DATA CLASSES
# ============================================================================

class ToolchainType(Enum):
    GCC = "gcc"
    LLVM = "llvm"

class CLibrary(Enum):
    GLIBC = "glibc"
    NEWLIB = "newlib"
    MUSL = "musl"
    NONE = "none"

class BuildMode(Enum):
    BARE_METAL = "bare"
    WITH_LIBC = "with-libc"

class TargetArchitecture:
    """Target architecture configuration"""
    def __init__(self, triple: str):
        self.triple = triple
        self.parse_triple()
    
    def parse_triple(self):
        """Parse target triple (e.g., x86_64-linux-gnu)"""
        parts = self.triple.split('-')
        self.arch = parts[0]
        self.vendor = parts[1] if len(parts) > 1 else 'unknown'
        self.os = parts[2] if len(parts) > 2 else 'none'
        self.env = parts[3] if len(parts) > 3 else 'gnu'
    
    @property
    def is_bare_metal(self) -> bool:
        """Check if target is bare metal"""
        return self.os in ['elf', 'none', 'eabi']
    
    @property
    def is_linux(self) -> bool:
        """Check if target is Linux"""
        return self.os == 'linux'
    
    @property
    def is_windows(self) -> bool:
        """Check if target is Windows"""
        return self.os == 'mingw32' or self.env == 'mingw32'

@dataclass
class BuildConfig:
    """Build configuration"""
    # Toolchain
    toolchain: ToolchainType = ToolchainType.GCC
    target: str = "x86_64-elf"
    prefix: Path = Path("./install")
    
    # Versions
    gcc_version: str = "13.2.0"
    binutils_version: str = "2.42"
    llvm_version: str = "17.0.6"
    
    # C Library
    c_library: CLibrary = CLibrary.NONE
    libc_version: Optional[str] = None
    build_mode: BuildMode = BuildMode.BARE_METAL
    
    # Components
    enable_languages: List[str] = field(default_factory=lambda: ["c", "c++"])
    enable_components: List[str] = field(default_factory=list)
    disable_components: List[str] = field(default_factory=list)
    
    # Build options
    jobs: int = field(default_factory=lambda: os.cpu_count() or 4)
    clean_build: bool = False
    keep_build_dir: bool = False
    enable_lto: bool = False
    enable_debug: bool = False
    enable_assertions: bool = False
    optimize: str = "2"  # -O2
    
    # Cross compilation
    sysroot: Optional[Path] = None
    with_sysroot: bool = False
    
    # Custom flags
    configure_flags: List[str] = field(default_factory=list)
    cmake_flags: List[str] = field(default_factory=list)
    cflags: List[str] = field(default_factory=list)
    cxxflags: List[str] = field(default_factory=list)
    ldflags: List[str] = field(default_factory=list)
    
    # Source and build directories
    source_dir: Path = Path("./sources")
    build_dir: Path = Path("./build")
    download_cache: Path = Path("./.cache/downloads")
    
    # Validation
    run_tests: bool = False
    validate_only: bool = False
    
    # GitHub Actions
    github_actions: bool = False
    upload_artifact: bool = False
    
    def __post_init__(self):
        """Post-initialization processing"""
        self.prefix = Path(self.prefix)
        self.source_dir = Path(self.source_dir)
        self.build_dir = Path(self.build_dir)
        self.download_cache = Path(self.download_cache)
        
        # Set default libc versions
        if self.libc_version is None:
            if self.c_library == CLibrary.GLIBC:
                self.libc_version = "2.38"
            elif self.c_library == CLibrary.NEWLIB:
                self.libc_version = "4.3.0"
            elif self.c_library == CLibrary.MUSL:
                self.libc_version = "1.2.4"
        
        # Parse target
        self.target_arch = TargetArchitecture(self.target)
        
        # Set sysroot if not specified
        if self.sysroot is None and self.with_sysroot:
            self.sysroot = self.prefix / self.target / "sysroot"
        
        # Set default components
        if self.toolchain == ToolchainType.LLVM:
            if not self.enable_components:
                self.enable_components = ["clang", "lld", "compiler-rt"]
            if not self.disable_components:
                self.disable_components = ["libcxx", "libcxxabi", "libunwind"]

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

class Color:
    """ANSI color codes"""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    MAGENTA = '\033[95m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    RESET = '\033[0m'

def log_info(msg: str):
    print(f"{Color.BLUE}[INFO]{Color.RESET} {msg}")

def log_success(msg: str):
    print(f"{Color.GREEN}[SUCCESS]{Color.RESET} {msg}")

def log_warning(msg: str):
    print(f"{Color.YELLOW}[WARNING]{Color.RESET} {msg}")

def log_error(msg: str):
    print(f"{Color.RED}[ERROR]{Color.RESET} {msg}")

def log_step(step: str, msg: str):
    print(f"\n{Color.CYAN}[{step}]{Color.RESET} {Color.BOLD}{msg}{Color.RESET}")

def run_command(cmd: Union[str, List[str]], cwd: Optional[Path] = None, 
                env: Optional[Dict] = None, capture: bool = False,
                check: bool = True, verbose: bool = False) -> subprocess.CompletedProcess:
    """
    Run shell command with error handling
    """
    # Prepare command
    if isinstance(cmd, str):
        if verbose:
            cmd_str = cmd
        args = cmd
        shell = True
    else:
        if verbose:
            cmd_str = ' '.join(shlex.quote(str(arg)) for arg in cmd)
        args = [str(arg) for arg in cmd]
        shell = False
    
    # Log command
    if verbose:
        log_info(f"Running: {cmd_str}")
        if cwd:
            log_info(f"  in: {cwd}")
    
    # Prepare environment
    current_env = os.environ.copy()
    if env:
        current_env.update(env)
    
    try:
        # Run command
        if capture:
            result = subprocess.run(
                args, shell=shell, cwd=cwd, env=current_env,
                capture_output=True, text=True, encoding='utf-8',
                errors='replace'
            )
        else:
            result = subprocess.run(
                args, shell=shell, cwd=cwd, env=current_env,
                text=True, encoding='utf-8', errors='replace'
            )
        
        # Check return code
        if check and result.returncode != 0:
            error_msg = f"Command failed with code {result.returncode}"
            if result.stderr:
                error_msg += f"\nStderr: {result.stderr[:500]}"
            raise subprocess.CalledProcessError(result.returncode, args, 
                                                result.stdout, result.stderr)
        
        return result
        
    except FileNotFoundError as e:
        raise RuntimeError(f"Command not found: {args[0] if isinstance(args, list) else args}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Command timeout: {e}") from e

def download_file(url: str, dest: Path, sha256: Optional[str] = None, 
                  verbose: bool = False) -> bool:
    """
    Download file with checksum verification
    """
    if dest.exists():
        if sha256:
            # Verify existing file
            with open(dest, 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            if file_hash == sha256:
                if verbose:
                    log_info(f"File already exists and verified: {dest}")
                return True
            else:
                log_warning(f"Checksum mismatch, re-downloading: {dest}")
                dest.unlink()
        else:
            if verbose:
                log_info(f"File already exists: {dest}")
            return True
    
    # Create directory
    dest.parent.mkdir(parents=True, exist_ok=True)
    
    # Download file
    log_info(f"Downloading: {url}")
    try:
        urllib.request.urlretrieve(url, dest)
    except Exception as e:
        raise RuntimeError(f"Failed to download {url}: {e}")
    
    # Verify checksum
    if sha256:
        with open(dest, 'rb') as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        if file_hash != sha256:
            dest.unlink()
            raise RuntimeError(f"Checksum verification failed for {dest}")
    
    log_success(f"Downloaded: {dest}")
    return True

def extract_archive(archive: Path, dest: Path, verbose: bool = False):
    """
    Extract archive (tar.gz, tar.xz, tar.bz2, zip)
    """
    log_info(f"Extracting: {archive} -> {dest}")
    
    dest.mkdir(parents=True, exist_ok=True)
    
    if archive.suffix in ['.gz', '.bz2', '.xz'] or '.tar.' in archive.name:
        # Tar archive
        try:
            with tarfile.open(archive, 'r:*') as tar:
                tar.extractall(dest)
        except tarfile.TarError as e:
            raise RuntimeError(f"Failed to extract tar archive: {e}")
    
    elif archive.suffix == '.zip':
        # Zip archive
        try:
            with zipfile.ZipFile(archive, 'r') as zip_ref:
                zip_ref.extractall(dest)
        except zipfile.BadZipFile as e:
            raise RuntimeError(f"Failed to extract zip archive: {e}")
    
    else:
        raise RuntimeError(f"Unsupported archive format: {archive}")
    
    if verbose:
        log_success(f"Extracted to: {dest}")

# ============================================================================
# SOURCE MANAGEMENT
# ============================================================================

class SourceManager:
    """Manage source code downloads and extraction"""
    
    # Source URLs
    GCC_MIRRORS = [
        "https://ftp.gnu.org/gnu/gcc/",
        "https://mirrors.kernel.org/gnu/gcc/",
        "https://ftpmirror.gnu.org/gcc/"
    ]
    
    BINUTILS_MIRRORS = [
        "https://ftp.gnu.org/gnu/binutils/",
        "https://mirrors.kernel.org/gnu/binutils/"
    ]
    
    LLVM_MIRRORS = [
        "https://github.com/llvm/llvm-project/releases/download/llvmorg-",
        "https://mirrors.edge.kernel.org/pub/llvm/"
    ]
    
    GLIBC_MIRRORS = [
        "https://ftp.gnu.org/gnu/glibc/",
        "https://mirrors.kernel.org/gnu/glibc/"
    ]
    
    NEWLIB_MIRRORS = [
        "https://sourceware.org/pub/newlib/",
        "https://mirrors.kernel.org/sourceware/newlib/"
    ]
    
    MUSL_MIRRORS = [
        "https://musl.libc.org/releases/"
    ]
    
    def __init__(self, config: BuildConfig):
        self.config = config
        self.source_dir = config.source_dir
        self.cache_dir = config.download_cache
        
        # Create directories
        self.source_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def get_gcc_source(self) -> Path:
        """Get GCC source directory"""
        version = self.config.gcc_version
        archive_name = f"gcc-{version}.tar.xz"
        cache_file = self.cache_dir / archive_name
        
        # Try mirrors
        for mirror in self.GCC_MIRRORS:
            url = f"{mirror}gcc-{version}/{archive_name}"
            try:
                download_file(url, cache_file, verbose=True)
                break
            except Exception as e:
                log_warning(f"Failed to download from {mirror}: {e}")
                continue
        else:
            raise RuntimeError(f"Failed to download GCC {version} from any mirror")
        
        # Extract
        extract_dir = self.source_dir / f"gcc-{version}"
        if not extract_dir.exists():
            extract_archive(cache_file, self.source_dir, verbose=True)
        
        return extract_dir
    
    def get_binutils_source(self) -> Path:
        """Get Binutils source directory"""
        version = self.config.binutils_version
        archive_name = f"binutils-{version}.tar.xz"
        cache_file = self.cache_dir / archive_name
        
        # Try mirrors
        for mirror in self.BINUTILS_MIRRORS:
            url = f"{mirror}{archive_name}"
            try:
                download_file(url, cache_file, verbose=True)
                break
            except Exception as e:
                log_warning(f"Failed to download from {mirror}: {e}")
                continue
        else:
            raise RuntimeError(f"Failed to download Binutils {version} from any mirror")
        
        # Extract
        extract_dir = self.source_dir / f"binutils-{version}"
        if not extract_dir.exists():
            extract_archive(cache_file, self.source_dir, verbose=True)
        
        return extract_dir
    
    def get_llvm_source(self) -> Path:
        """Get LLVM source directory"""
        version = self.config.llvm_version
        archive_name = f"llvm-project-{version}.src.tar.xz"
        cache_file = self.cache_dir / archive_name
        
        # Try mirrors
        for mirror in self.LLVM_MIRRORS:
            url = f"{mirror}{version}/{archive_name}"
            try:
                download_file(url, cache_file, verbose=True)
                break
            except Exception as e:
                # Try alternative naming
                alt_name = f"llvm-project-{version}.tar.xz"
                alt_url = f"{mirror}{version}/{alt_name}"
                try:
                    download_file(alt_url, self.cache_dir / alt_name, verbose=True)
                    cache_file = self.cache_dir / alt_name
                    archive_name = alt_name
                    break
                except Exception:
                    continue
        else:
            raise RuntimeError(f"Failed to download LLVM {version} from any mirror")
        
        # Extract
        extract_dir = self.source_dir / f"llvm-project-{version}"
        if not extract_dir.exists():
            extract_archive(cache_file, self.source_dir, verbose=True)
        
        return extract_dir
    
    def get_libc_source(self) -> Optional[Path]:
        """Get C library source directory"""
        if self.config.c_library == CLibrary.NONE:
            return None
        
        version = self.config.libc_version
        if not version:
            raise ValueError(f"Version required for {self.config.c_library}")
        
        if self.config.c_library == CLibrary.GLIBC:
            archive_name = f"glibc-{version}.tar.xz"
            cache_file = self.cache_dir / archive_name
            
            for mirror in self.GLIBC_MIRRORS:
                url = f"{mirror}{archive_name}"
                try:
                    download_file(url, cache_file, verbose=True)
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError(f"Failed to download glibc {version}")
            
            extract_dir = self.source_dir / f"glibc-{version}"
        
        elif self.config.c_library == CLibrary.NEWLIB:
            archive_name = f"newlib-{version}.tar.gz"
            cache_file = self.cache_dir / archive_name
            
            for mirror in self.NEWLIB_MIRRORS:
                url = f"{mirror}{archive_name}"
                try:
                    download_file(url, cache_file, verbose=True)
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError(f"Failed to download newlib {version}")
            
            extract_dir = self.source_dir / f"newlib-{version}"
        
        elif self.config.c_library == CLibrary.MUSL:
            archive_name = f"musl-{version}.tar.gz"
            cache_file = self.cache_dir / archive_name
            
            for mirror in self.MUSL_MIRRORS:
                url = f"{mirror}{archive_name}"
                try:
                    download_file(url, cache_file, verbose=True)
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError(f"Failed to download musl {version}")
            
            extract_dir = self.source_dir / f"musl-{version}"
        
        else:
            return None
        
        # Extract
        if not extract_dir.exists():
            extract_archive(cache_file, self.source_dir, verbose=True)
        
        return extract_dir

# ============================================================================
# BUILDERS
# ============================================================================

class ToolchainBuilder:
    """Base class for toolchain builders"""
    
    def __init__(self, config: BuildConfig):
        self.config = config
        self.source_mgr = SourceManager(config)
        self.build_env = self._prepare_build_env()
    
    def _prepare_build_env(self) -> Dict[str, str]:
        """Prepare build environment variables"""
        env = os.environ.copy()
        
        # Set compiler flags
        if self.config.cflags:
            env['CFLAGS'] = ' '.join(self.config.cflags)
        if self.config.cxxflags:
            env['CXXFLAGS'] = ' '.join(self.config.cxxflags)
        if self.config.ldflags:
            env['LDFLAGS'] = ' '.join(self.config.ldflags)
        
        # Optimization
        env['CFLAGS'] = f"-O{self.config.optimize} {env.get('CFLAGS', '')}"
        env['CXXFLAGS'] = f"-O{self.config.optimize} {env.get('CXXFLAGS', '')}"
        
        # Parallel build
        env['MAKEFLAGS'] = f"-j{self.config.jobs}"
        
        return env
    
    def build(self) -> bool:
        """Build toolchain - to be implemented by subclasses"""
        raise NotImplementedError

class GCCBuilder(ToolchainBuilder):
    """Build GCC + Binutils toolchain"""
    
    def build(self) -> bool:
        log_step("GCC", f"Building GCC {self.config.gcc_version} + Binutils {self.config.binutils_version}")
        
        try:
            # Get sources
            gcc_src = self.source_mgr.get_gcc_source()
            binutils_src = self.source_mgr.get_binutils_source()
            
            # Build binutils first
            self._build_binutils(binutils_src)
            
            # Build GCC
            self._build_gcc(gcc_src)
            
            # Build C library if needed
            if self.config.c_library != CLibrary.NONE:
                self._build_libc()
            
            log_success("GCC toolchain built successfully")
            return True
            
        except Exception as e:
            log_error(f"Failed to build GCC toolchain: {e}")
            return False
    
    def _build_binutils(self, src_dir: Path):
        """Build and install binutils"""
        log_step("Binutils", "Building binutils")
        
        build_dir = self.config.build_dir / "binutils"
        if self.config.clean_build and build_dir.exists():
            shutil.rmtree(build_dir)
        
        build_dir.mkdir(parents=True, exist_ok=True)
        
        # Configure
        configure_cmd = [
            str(src_dir / "configure"),
            f"--target={self.config.target}",
            f"--prefix={self.config.prefix}",
            "--disable-nls",
            "--disable-werror",
            "--disable-multilib",
            "--with-sysroot" if self.config.with_sysroot else "",
            "--enable-gold",
            "--enable-plugins",
            "--enable-deterministic-archives",
            *self.config.configure_flags
        ]
        
        # Filter out empty strings
        configure_cmd = [arg for arg in configure_cmd if arg]
        
        run_command(configure_cmd, cwd=build_dir, env=self.build_env, verbose=True)
        
        # Build and install
        run_command(["make", f"-j{self.config.jobs}"], cwd=build_dir, 
                   env=self.build_env, verbose=True)
        run_command(["make", "install"], cwd=build_dir, env=self.build_env, verbose=True)
    
    def _build_gcc(self, src_dir: Path):
        """Build and install GCC"""
        log_step("GCC", "Building GCC")
        
        # Add binutils to PATH
        env = self.build_env.copy()
        env['PATH'] = f"{self.config.prefix / 'bin'}:{env['PATH']}"
        
        build_dir = self.config.build_dir / "gcc"
        if self.config.clean_build and build_dir.exists():
            shutil.rmtree(build_dir)
        
        build_dir.mkdir(parents=True, exist_ok=True)
        
        # Download prerequisites
        log_info("Downloading GCC prerequisites")
        run_command([str(src_dir / "contrib" / "download_prerequisites")], 
                   cwd=src_dir, env=env, verbose=True)
        
        # Configure
        configure_cmd = [
            str(src_dir / "configure"),
            f"--target={self.config.target}",
            f"--prefix={self.config.prefix}",
            f"--enable-languages={','.join(self.config.enable_languages)}",
            "--disable-nls",
            "--disable-multilib",
            "--without-headers" if self.config.c_library == CLibrary.NONE else "",
            f"--with-sysroot={self.config.sysroot}" if self.config.sysroot else "",
            "--disable-libssp",
            "--disable-libstdcxx-pch",
            "--disable-libgomp",
            "--disable-libmudflap",
            "--enable-checking=release",
            "--with-gnu-as",
            "--with-gnu-ld",
            *self.config.configure_flags
        ]
        
        # Filter out empty strings
        configure_cmd = [arg for arg in configure_cmd if arg]
        
        run_command(configure_cmd, cwd=build_dir, env=env, verbose=True)
        
        # Build
        run_command(["make", f"-j{self.config.jobs}", "all-gcc", "all-target-libgcc"], 
                   cwd=build_dir, env=env, verbose=True)
        
        # Install
        run_command(["make", "install-gcc", "install-target-libgcc"], 
                   cwd=build_dir, env=env, verbose=True)
        
        # If we have a C library, build the rest
        if self.config.c_library != CLibrary.NONE:
            run_command(["make", f"-j{self.config.jobs}"], cwd=build_dir, env=env, verbose=True)
            run_command(["make", "install"], cwd=build_dir, env=env, verbose=True)
    
    def _build_libc(self):
        """Build C library"""
        log_step("LibC", f"Building {self.config.c_library.value}")
        
        src_dir = self.source_mgr.get_libc_source()
        if not src_dir:
            return
        
        env = self.build_env.copy()
        env['PATH'] = f"{self.config.prefix / 'bin'}:{env['PATH']}"
        env['CC'] = f"{self.config.target}-gcc"
        env['CXX'] = f"{self.config.target}-g++"
        
        build_dir = self.config.build_dir / self.config.c_library.value
        if self.config.clean_build and build_dir.exists():
            shutil.rmtree(build_dir)
        
        build_dir.mkdir(parents=True, exist_ok=True)
        
        if self.config.c_library == CLibrary.GLIBC:
            self._build_glibc(src_dir, build_dir, env)
        elif self.config.c_library == CLibrary.NEWLIB:
            self._build_newlib(src_dir, build_dir, env)
        elif self.config.c_library == CLibrary.MUSL:
            self._build_musl(src_dir, build_dir, env)
    
    def _build_glibc(self, src_dir: Path, build_dir: Path, env: Dict):
        """Build glibc"""
        # Configure
        configure_cmd = [
            str(src_dir / "configure"),
            f"--host={self.config.target}",
            f"--prefix=/usr",
            f"--with-headers={self.config.sysroot / 'usr' / 'include'}" if self.config.sysroot else "",
            "--disable-werror",
            "--enable-obsolete-rpc" if self.config.target_arch.is_linux else "",
            *self.config.configure_flags
        ]
        
        run_command(configure_cmd, cwd=build_dir, env=env, verbose=True)
        
        # Build and install to sysroot
        run_command(["make", f"-j{self.config.jobs}"], cwd=build_dir, env=env, verbose=True)
        
        if self.config.sysroot:
            run_command(["make", f"DESTDIR={self.config.sysroot}", "install"], 
                       cwd=build_dir, env=env, verbose=True)
    
    def _build_newlib(self, src_dir: Path, build_dir: Path, env: Dict):
        """Build newlib"""
        # Configure
        configure_cmd = [
            str(src_dir / "configure"),
            f"--target={self.config.target}",
            f"--prefix={self.config.prefix}",
            "--disable-nls",
            "--disable-newlib-supplied-syscalls",
            "--enable-multilib",
            *self.config.configure_flags
        ]
        
        run_command(configure_cmd, cwd=build_dir, env=env, verbose=True)
        
        # Build and install
        run_command(["make", f"-j{self.config.jobs}"], cwd=build_dir, env=env, verbose=True)
        run_command(["make", "install"], cwd=build_dir, env=env, verbose=True)
    
    def _build_musl(self, src_dir: Path, build_dir: Path, env: Dict):
        """Build musl"""
        # Configure
        configure_cmd = [
            str(src_dir / "configure"),
            f"--target={self.config.target}",
            f"--prefix={self.config.prefix}",
            "--disable-shared",
            "--enable-static",
            *self.config.configure_flags
        ]
        
        run_command(configure_cmd, cwd=build_dir, env=env, verbose=True)
        
        # Build and install
        run_command(["make", f"-j{self.config.jobs}"], cwd=build_dir, env=env, verbose=True)
        run_command(["make", "install"], cwd=build_dir, env=env, verbose=True)

class LLVMBuilder(ToolchainBuilder):
    """Build LLVM/Clang toolchain"""
    
    def build(self) -> bool:
        log_step("LLVM", f"Building LLVM {self.config.llvm_version}")
        
        try:
            # Get sources
            llvm_src = self.source_mgr.get_llvm_source()
            
            # Build LLVM
            self._build_llvm(llvm_src)
            
            # Build C library if needed
            if self.config.c_library != CLibrary.NONE:
                self._build_libc_for_llvm()
            
            log_success("LLVM toolchain built successfully")
            return True
            
        except Exception as e:
            log_error(f"Failed to build LLVM toolchain: {e}")
            return False
    
    def _build_llvm(self, src_dir: Path):
        """Build and install LLVM"""
        build_dir = self.config.build_dir / "llvm"
        if self.config.clean_build and build_dir.exists():
            shutil.rmtree(build_dir)
        
        build_dir.mkdir(parents=True, exist_ok=True)
        
        # Prepare CMake configuration
        cmake_cmd = [
            "cmake",
            str(src_dir / "llvm"),
            f"-DCMAKE_INSTALL_PREFIX={self.config.prefix}",
            f"-DCMAKE_BUILD_TYPE={'Debug' if self.config.enable_debug else 'Release'}",
            f"-DLLVM_ENABLE_PROJECTS={','.join(self.config.enable_components)}",
            f"-DLLVM_TARGETS_TO_BUILD={'X86;AArch64;ARM;RISCV' if not self.config.target_arch.arch else self._get_llvm_target(self.config.target_arch.arch)}",
            f"-DLLVM_DEFAULT_TARGET_TRIPLE={self.config.target}",
            "-DLLVM_ENABLE_ASSERTIONS=ON" if self.config.enable_assertions else "-DLLVM_ENABLE_ASSERTIONS=OFF",
            "-DLLVM_ENABLE_LTO=ON" if self.config.enable_lto else "-DLLVM_ENABLE_LTO=OFF",
            "-DLLVM_INCLUDE_TESTS=OFF",
            "-DLLVM_INCLUDE_EXAMPLES=OFF",
            "-DLLVM_INCLUDE_BENCHMARKS=OFF",
            "-DLLVM_ENABLE_TERMINFO=OFF",
            "-DLLVM_ENABLE_ZLIB=OFF",
            "-DLLVM_ENABLE_ZSTD=OFF",
            "-G", "Ninja",
            *self.config.cmake_flags
        ]
        
        run_command(cmake_cmd, cwd=build_dir, env=self.build_env, verbose=True)
        
        # Build and install
        run_command(["ninja", f"-j{self.config.jobs}"], cwd=build_dir, 
                   env=self.build_env, verbose=True)
        run_command(["ninja", "install"], cwd=build_dir, env=self.build_env, verbose=True)
    
    def _get_llvm_target(self, arch: str) -> str:
        """Convert architecture to LLVM target name"""
        arch_map = {
            'x86_64': 'X86',
            'i386': 'X86',
            'i686': 'X86',
            'aarch64': 'AArch64',
            'arm': 'ARM',
            'riscv32': 'RISCV',
            'riscv64': 'RISCV',
            'mips': 'Mips',
            'powerpc': 'PowerPC'
        }
        return arch_map.get(arch.lower(), 'X86;AArch64;ARM;RISCV')
    
    def _build_libc_for_llvm(self):
        """Build C library for LLVM toolchain"""
        # Similar to GCC builder but with clang
        pass

# ============================================================================
# VALIDATION AND INSTALLATION
# ============================================================================

class ToolchainValidator:
    """Validate built toolchain"""
    
    def __init__(self, config: BuildConfig):
        self.config = config
    
    def validate(self) -> bool:
        """Validate the built toolchain"""
        log_step("VALIDATION", "Validating toolchain")
        
        try:
            # Check if binaries exist
            if not self._check_binaries():
                return False
            
            # Test compilation
            if not self._test_compilation():
                return False
            
            # Run tests if requested
            if self.config.run_tests:
                if not self._run_tests():
                    return False
            
            log_success("Toolchain validation passed")
            return True
            
        except Exception as e:
            log_error(f"Validation failed: {e}")
            return False
    
    def _check_binaries(self) -> bool:
        """Check if required binaries exist"""
        required_bins = []
        
        if self.config.toolchain == ToolchainType.GCC:
            required_bins = [
                f"{self.config.target}-gcc",
                f"{self.config.target}-g++",
                f"{self.config.target}-ld",
                f"{self.config.target}-ar",
                f"{self.config.target}-as",
                f"{self.config.target}-objcopy"
            ]
        else:  # LLVM
            required_bins = ["clang", "clang++", "lld", "llvm-ar"]
        
        bin_dir = self.config.prefix / "bin"
        missing = []
        
        for bin_name in required_bins:
            bin_path = bin_dir / bin_name
            if not bin_path.exists():
                missing.append(bin_name)
        
        if missing:
            log_error(f"Missing binaries: {', '.join(missing)}")
            return False
        
        log_info(f"All required binaries found in {bin_dir}")
        return True
    
    def _test_compilation(self) -> bool:
        """Test compilation with a simple program"""
        test_dir = self.config.build_dir / "test"
        test_dir.mkdir(parents=True, exist_ok=True)
        
        # Create test C program
        test_c = test_dir / "test.c"
        test_c.write_text("""
#include <stdio.h>
int main() {
    printf("Hello from %s\\n", __TARGET__);
    return 0;
}
""".replace("__TARGET__", self.config.target))
        
        # Compile
        if self.config.toolchain == ToolchainType.GCC:
            compiler = self.config.prefix / "bin" / f"{self.config.target}-gcc"
        else:
            compiler = self.config.prefix / "bin" / "clang"
        
        compile_cmd = [
            str(compiler),
            str(test_c),
            "-o",
            str(test_dir / "test.elf"),
            "-v"
        ]
        
        try:
            result = run_command(compile_cmd, capture=True, verbose=True)
            if result.returncode == 0:
                log_info("Test compilation successful")
                return True
            else:
                log_error(f"Test compilation failed: {result.stderr}")
                return False
        except Exception as e:
            log_error(f"Test compilation failed: {e}")
            return False
    
    def _run_tests(self) -> bool:
        """Run toolchain tests"""
        # This would run comprehensive tests
        log_info("Running toolchain tests...")
        # Implement actual tests here
        return True

class ToolchainInstaller:
    """Install and package toolchain"""
    
    def __init__(self, config: BuildConfig):
        self.config = config
    
    def install(self) -> bool:
        """Install toolchain to system location"""
        log_step("INSTALLATION", f"Installing toolchain to {self.config.prefix}")
        
        try:
            # Create installation directory
            self.config.prefix.mkdir(parents=True, exist_ok=True)
            
            # Create version file
            self._create_version_file()
            
            # Create environment setup script
            self._create_env_script()
            
            # Create package if requested
            if self.config.github_actions and self.config.upload_artifact:
                self._create_package()
            
            log_success(f"Toolchain installed to {self.config.prefix}")
            return True
            
        except Exception as e:
            log_error(f"Installation failed: {e}")
            return False
    
    def _create_version_file(self):
        """Create version information file"""
        version_file = self.config.prefix / "VERSION.txt"
        
        version_info = f"""
Toolchain: {self.config.toolchain.value.upper()}
Target: {self.config.target}
Build date: {os.popen('date').read().strip()}

Versions:
  - Toolchain: {self.config.gcc_version if self.config.toolchain == ToolchainType.GCC else self.config.llvm_version}
  - Binutils: {self.config.binutils_version if self.config.toolchain == ToolchainType.GCC else 'N/A'}
  - C Library: {self.config.c_library.value} {self.config.libc_version or ''}

Configuration:
  - Prefix: {self.config.prefix}
  - Languages: {', '.join(self.config.enable_languages)}
  - Optimization: -O{self.config.optimize}
  - LTO: {'Enabled' if self.config.enable_lto else 'Disabled'}
  - Debug: {'Enabled' if self.config.enable_debug else 'Disabled'}

Environment:
  - PATH: {self.config.prefix / 'bin'}
  - CC: {self.config.target}-gcc
  - CXX: {self.config.target}-g++
  
Use 'source {self.config.prefix / 'environment'}' to setup environment.
"""
        
        version_file.write_text(version_info)
        log_info(f"Version file created: {version_file}")
    
    def _create_env_script(self):
        """Create environment setup script"""
        env_file = self.config.prefix / "environment"
        
        env_script = f"""#!/bin/bash
# Toolchain environment setup for {self.config.target}

export TOOLCHAIN_PREFIX="{self.config.prefix}"
export TOOLCHAIN_TARGET="{self.config.target}"
export PATH="${{TOOLCHAIN_PREFIX}}/bin:${{PATH}}"

export CC="${{TOOLCHAIN_TARGET}}-gcc"
export CXX="${{TOOLCHAIN_TARGET}}-g++"
export AR="${{TOOLCHAIN_TARGET}}-ar"
export AS="${{TOOLCHAIN_TARGET}}-as"
export LD="${{TOOLCHAIN_TARGET}}-ld"
export STRIP="${{TOOLCHAIN_TARGET}}-strip"
export OBJCOPY="${{TOOLCHAIN_TARGET}}-objcopy"
export OBJDUMP="${{TOOLCHAIN_TARGET}}-objdump"
export RANLIB="${{TOOLCHAIN_TARGET}}-ranlib"
export READELF="${{TOOLCHAIN_TARGET}}-readelf"

# For LLVM toolchain
if [ -f "${{TOOLCHAIN_PREFIX}}/bin/clang" ]; then
    export CLANG_CC="${{TOOLCHAIN_PREFIX}}/bin/clang"
    export CLANG_CXX="${{TOOLCHAIN_PREFIX}}/bin/clang++"
fi

# Sysroot if available
if [ -d "${{TOOLCHAIN_PREFIX}}/${{TOOLCHAIN_TARGET}}/sysroot" ]; then
    export SYSROOT="${{TOOLCHAIN_PREFIX}}/${{TOOLCHAIN_TARGET}}/sysroot"
    export CFLAGS="${{CFLAGS}} --sysroot=${{SYSROOT}}"
    export CXXFLAGS="${{CXXFLAGS}} --sysroot=${{SYSROOT}}"
    export LDFLAGS="${{LDFLAGS}} --sysroot=${{SYSROOT}}"
fi

echo "Toolchain environment set for ${{TOOLCHAIN_TARGET}}"
echo "Prefix: ${{TOOLCHAIN_PREFIX}}"
"""
        
        env_file.write_text(env_script)
        env_file.chmod(0o755)
        log_info(f"Environment script created: {env_file}")
    
    def _create_package(self):
        """Create distributable package"""
        log_step("PACKAGING", "Creating distributable package")
        
        package_name = f"{self.config.toolchain.value}-{self.config.target}-{self.config.gcc_version if self.config.toolchain == ToolchainType.GCC else self.config.llvm_version}"
        package_file = self.config.build_dir / f"{package_name}.tar.xz"
        
        # Create archive
        with tarfile.open(package_file, "w:xz") as tar:
            tar.add(self.config.prefix, arcname=package_name)
        
        # Create checksum
        checksum_file = package_file.with_suffix(".tar.xz.sha256")
        with open(package_file, "rb") as f:
            checksum = hashlib.sha256(f.read()).hexdigest()
        checksum_file.write_text(f"{checksum}  {package_file.name}")
        
        log_success(f"Package created: {package_file}")
        log_success(f"Checksum: {checksum_file}")

# ============================================================================
# GITHUB ACTIONS INTEGRATION
# ============================================================================

class GitHubActions:
    """GitHub Actions integration"""
    
    @staticmethod
    def generate_workflow(config: BuildConfig) -> str:
        """Generate GitHub Actions workflow file"""
        
        workflow = f"""name: 🏗️ Build Cross Compiler Toolchain

on:
  workflow_dispatch:
    inputs:
      toolchain:
        description: 'Toolchain to build'
        required: true
        default: '{config.toolchain.value}'
        type: choice
        options:
          - gcc
          - llvm
      
      target:
        description: 'Target architecture'
        required: true
        default: '{config.target}'
        type: string
      
      c_library:
        description: 'C library to use'
        required: false
        default: '{config.c_library.value}'
        type: choice
        options:
          - glibc
          - newlib
          - musl
          - none
      
      gcc_version:
        description: 'GCC version (if using GCC)'
        required: false
        default: '{config.gcc_version}'
        type: string
      
      llvm_version:
        description: 'LLVM version (if using LLVM)'
        required: false  
        default: '{config.llvm_version}'
        type: string

jobs:
  build:
    runs-on: ubuntu-22.04
    
    steps:
    - name: ⬇️ Checkout code
      uses: actions/checkout@v4
    
    - name: 🐍 Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.11'
        cache: 'pip'
    
    - name: 📦 Install dependencies
      run: |
        sudo apt-get update
        sudo apt-get install -y \\
          build-essential \\
          bison \\
          flex \\
          libgmp-dev \\
          libmpfr-dev \\
          libmpc-dev \\
          texinfo \\
          libisl-dev \\
          ninja-build \\
          cmake \\
          git \\
          wget \\
          xz-utils \\
          python3-pip
    
    - name: 🏗️ Build toolchain
      run: |
        python3 build_toolchain.py \\
          --toolchain ${{{{ github.event.inputs.toolchain }}}} \\
          --target ${{{{ github.event.inputs.target }}}} \\
          --c-library ${{{{ github.event.inputs.c_library }}}} \\
          {'--gcc-version ${{ github.event.inputs.gcc_version }}' if config.toolchain == ToolchainType.GCC else '--llvm-version ${{ github.event.inputs.llvm_version }}'} \\
          --prefix ./install \\
          --jobs $(nproc) \\
          --clean-build \\
          --run-tests \\
          --upload-artifact
    
    - name: 📤 Upload artifact
      uses: actions/upload-artifact@v4
      with:
        name: ${{{{ github.event.inputs.toolchain }}}}-${{{{ github.event.inputs.target }}}}
        path: |
          ./build/*.tar.xz
          ./build/*.sha256
        retention-days: 7
"""
        
        return workflow
    
    @staticmethod
    def save_workflow(config: BuildConfig, path: Path):
        """Save GitHub Actions workflow to file"""
        workflow = GitHubActions.generate_workflow(config)
        path.write_text(workflow)
        log_success(f"GitHub Actions workflow saved to {path}")

# ============================================================================
# MAIN PROGRAM
# ============================================================================

def create_parser() -> argparse.ArgumentParser:
    """Create command line argument parser"""
    
    parser = argparse.ArgumentParser(
        description="🏗️ Cross Compiler Builder Pro - Build custom GCC/Binutils or LLVM toolchains",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Build GCC toolchain for x86_64-elf (bare metal)
  python3 build_toolchain.py --toolchain gcc --target x86_64-elf
  
  # Build GCC with newlib for ARM
  python3 build_toolchain.py --toolchain gcc --target arm-none-eabi --c-library newlib
  
  # Build LLVM toolchain for RISC-V
  python3 build_toolchain.py --toolchain llvm --target riscv64-unknown-elf
  
  # Build with custom options
  python3 build_toolchain.py --toolchain gcc --target aarch64-linux-gnu \\
    --c-library glibc --gcc-version 12.3.0 --enable-languages c,c++,fortran \\
    --prefix /opt/cross --jobs 8 --enable-lto
  
  # Generate GitHub Actions workflow
  python3 build_toolchain.py --generate-workflow --output .github/workflows/build.yml
  
  # Validate existing toolchain
  python3 build_toolchain.py --validate --prefix /opt/cross --target x86_64-elf
"""
    )
    
    # Toolchain selection
    toolchain_group = parser.add_argument_group('Toolchain Selection')
    toolchain_group.add_argument(
        '--toolchain', '-t',
        choices=['gcc', 'llvm'],
        default='gcc',
        help='Toolchain to build (default: gcc)'
    )
    toolchain_group.add_argument(
        '--target',
        required=True,
        help='Target architecture (e.g., x86_64-elf, arm-none-eabi, aarch64-linux-gnu)'
    )
    
    # Version selection
    version_group = parser.add_argument_group('Version Selection')
    version_group.add_argument(
        '--gcc-version',
        default='13.2.0',
        help='GCC version (default: 13.2.0)'
    )
    version_group.add_argument(
        '--binutils-version',
        default='2.42',
        help='Binutils version (default: 2.42)'
    )
    version_group.add_argument(
        '--llvm-version',
        default='17.0.6',
        help='LLVM version (default: 17.0.6)'
    )
    
    # C Library selection
    libc_group = parser.add_argument_group('C Library Selection')
    libc_group.add_argument(
        '--c-library',
        choices=['glibc', 'newlib', 'musl', 'none'],
        default='none',
        help='C library to use (default: none)'
    )
    libc_group.add_argument(
        '--libc-version',
        help='C library version (default: auto)'
    )
    libc_group.add_argument(
        '--with-sysroot',
        action='store_true',
        help='Build with sysroot support'
    )
    
    # Build configuration
    build_group = parser.add_argument_group('Build Configuration')
    build_group.add_argument(
        '--prefix',
        default='./install',
        help='Installation prefix (default: ./install)'
    )
    build_group.add_argument(
        '--jobs', '-j',
        type=int,
        default=0,
        help='Number of parallel jobs (0 = auto, default: auto)'
    )
    build_group.add_argument(
        '--enable-languages',
        default='c,c++',
        help='Languages to enable (comma-separated, default: c,c++)'
    )
    build_group.add_argument(
        '--enable-components',
        help='LLVM components to enable (comma-separated)'
    )
    build_group.add_argument(
        '--disable-components',
        help='LLVM components to disable (comma-separated)'
    )
    
    # Build options
    options_group = parser.add_argument_group('Build Options')
    options_group.add_argument(
        '--clean-build',
        action='store_true',
        help='Clean build directories before building'
    )
    options_group.add_argument(
        '--keep-build-dir',
        action='store_true',
        help='Keep build directory after installation'
    )
    options_group.add_argument(
        '--enable-lto',
        action='store_true',
        help='Enable Link Time Optimization'
    )
    options_group.add_argument(
        '--enable-debug',
        action='store_true',
        help='Build with debug symbols'
    )
    options_group.add_argument(
        '--enable-assertions',
        action='store_true',
        help='Enable assertions (LLVM only)'
    )
    options_group.add_argument(
        '--optimize',
        choices=['0', '1', '2', '3', 's', 'z', 'fast'],
        default='2',
        help='Optimization level (default: 2)'
    )
    
    # Custom flags
    flags_group = parser.add_argument_group('Custom Flags')
    flags_group.add_argument(
        '--configure-flag',
        action='append',
        dest='configure_flags',
        default=[],
        help='Additional configure flag (can be used multiple times)'
    )
    flags_group.add_argument(
        '--cmake-flag',
        action='append',
        dest='cmake_flags',
        default=[],
        help='Additional CMake flag (can be used multiple times)'
    )
    flags_group.add_argument(
        '--cflag',
        action='append',
        dest='cflags',
        default=[],
        help='Additional CFLAG (can be used multiple times)'
    )
    flags_group.add_argument(
        '--cxxflag',
        action='append',
        dest='cxxflags',
        default=[],
        help='Additional CXXFLAG (can be used multiple times)'
    )
    flags_group.add_argument(
        '--ldflag',
        action='append',
        dest='ldflags',
        default=[],
        help='Additional LDFLAG (can be used multiple times)'
    )
    
    # Validation and testing
    validation_group = parser.add_argument_group('Validation and Testing')
    validation_group.add_argument(
        '--run-tests',
        action='store_true',
        help='Run tests after building'
    )
    validation_group.add_argument(
        '--validate-only',
        action='store_true',
        help='Only validate existing toolchain, do not build'
    )
    
    # GitHub Actions
    github_group = parser.add_argument_group('GitHub Actions')
    github_group.add_argument(
        '--github-actions',
        action='store_true',
        help='Enable GitHub Actions integration'
    )
    github_group.add_argument(
        '--upload-artifact',
        action='store_true',
        help='Upload artifact after build (for CI)'
    )
    github_group.add_argument(
        '--generate-workflow',
        action='store_true',
        help='Generate GitHub Actions workflow file'
    )
    github_group.add_argument(
        '--workflow-output',
        default='.github/workflows/build.yml',
        help='Output path for workflow file (default: .github/workflows/build.yml)'
    )
    
    # Source and build directories
    dir_group = parser.add_argument_group('Directories')
    dir_group.add_argument(
        '--source-dir',
        default='./sources',
        help='Source code directory (default: ./sources)'
    )
    dir_group.add_argument(
        '--build-dir',
        default='./build',
        help='Build directory (default: ./build)'
    )
    dir_group.add_argument(
        '--cache-dir',
        default='./.cache/downloads',
        help='Download cache directory (default: ./.cache/downloads)'
    )
    
    # Other
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    
    return parser

def main():
    """Main program entry point"""
    
    # Parse arguments
    parser = create_parser()
    args = parser.parse_args()
    
    # Set jobs
    if args.jobs == 0:
        args.jobs = os.cpu_count() or 4
    
    # Create build config
    config = BuildConfig(
        toolchain=ToolchainType(args.toolchain),
        target=args.target,
        prefix=Path(args.prefix),
        
        # Versions
        gcc_version=args.gcc_version,
        binutils_version=args.binutils_version,
        llvm_version=args.llvm_version,
        
        # C Library
        c_library=CLibrary(args.c_library),
        libc_version=args.libc_version,
        build_mode=BuildMode.BARE_METAL,  # Will be auto-detected
        
        # Components
        enable_languages=args.enable_languages.split(','),
        enable_components=args.enable_components.split(',') if args.enable_components else [],
        disable_components=args.disable_components.split(',') if args.disable_components else [],
        
        # Build options
        jobs=args.jobs,
        clean_build=args.clean_build,
        keep_build_dir=args.keep_build_dir,
        enable_lto=args.enable_lto,
        enable_debug=args.enable_debug,
        enable_assertions=args.enable_assertions,
        optimize=args.optimize,
        
        # Cross compilation
        with_sysroot=args.with_sysroot,
        
        # Custom flags
        configure_flags=args.configure_flags,
        cmake_flags=args.cmake_flags,
        cflags=args.cflags,
        cxxflags=args.cxxflags,
        ldflags=args.ldflags,
        
        # Directories
        source_dir=Path(args.source_dir),
        build_dir=Path(args.build_dir),
        download_cache=Path(args.cache_dir),
        
        # Validation
        run_tests=args.run_tests,
        validate_only=args.validate_only,
        
        # GitHub Actions
        github_actions=args.github_actions,
        upload_artifact=args.upload_artifact
    )
    
    # Generate GitHub Actions workflow if requested
    if args.generate_workflow:
        workflow_path = Path(args.workflow_output)
        workflow_path.parent.mkdir(parents=True, exist_ok=True)
        GitHubActions.save_workflow(config, workflow_path)
        return 0
    
    print(f"""
{Color.BOLD}{Color.CYAN}🏗️ Cross Compiler Builder Pro{Color.RESET}
{Color.BOLD}Target:     {Color.GREEN}{config.target}{Color.RESET}
{Color.BOLD}Toolchain:  {Color.GREEN}{config.toolchain.value.upper()}{Color.RESET}
{Color.BOLD}C Library:  {Color.GREEN}{config.c_library.value}{Color.RESET}
{Color.BOLD}Prefix:     {Color.GREEN}{config.prefix}{Color.RESET}
{Color.BOLD}Jobs:       {Color.GREEN}{config.jobs}{Color.RESET}
    """)
    
    # Validate only mode
    if args.validate_only:
        validator = ToolchainValidator(config)
        if validator.validate():
            log_success("Toolchain validation passed")
            return 0
        else:
            log_error("Toolchain validation failed")
            return 1
    
    # Build toolchain
    try:
        # Create builder
        if config.toolchain == ToolchainType.GCC:
            builder = GCCBuilder(config)
        else:
            builder = LLVMBuilder(config)
        
        # Build
        if not builder.build():
            log_error("Build failed")
            return 1
        
        # Validate
        validator = ToolchainValidator(config)
        if not validator.validate():
            log_error("Validation failed")
            return 1
        
        # Install
        installer = ToolchainInstaller(config)
        if not installer.install():
            log_error("Installation failed")
            return 1
        
        # Clean up if requested
        if not config.keep_build_dir and config.build_dir.exists():
            shutil.rmtree(config.build_dir)
        
        print(f"""
{Color.BOLD}{Color.GREEN}✅ Build completed successfully!{Color.RESET}

Your toolchain is installed at: {Color.CYAN}{config.prefix}{Color.RESET}

To use the toolchain:
  1. Source the environment file:
     {Color.CYAN}source {config.prefix}/environment{Color.RESET}
  
  2. Or add to your PATH:
     {Color.CYAN}export PATH="{config.prefix}/bin:$PATH"{Color.RESET}
  
  3. Compile your code:
     {Color.CYAN}{config.target}-gcc -o program program.c{Color.RESET}

For GitHub Actions, use: {Color.CYAN}--generate-workflow{Color.RESET}
        """)
        
        return 0
        
    except KeyboardInterrupt:
        log_error("Build interrupted by user")
        return 1
    except Exception as e:
        log_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())