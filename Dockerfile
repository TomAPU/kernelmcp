# -------- Build stage for fresh LLVM/clang/clangd-indexer -------------
FROM ubuntu:24.04 AS llvm-build
ARG DEBIAN_FRONTEND=noninteractive
ARG LLVM_VER=15.0.7
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git cmake ninja-build build-essential python3 ca-certificates curl && \
    rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch llvmorg-${LLVM_VER} https://github.com/llvm/llvm-project.git /tmp/llvm-project
# Build shared libs and install everything to /opt/llvm
RUN cmake -S /tmp/llvm-project/llvm -B /tmp/llvm-build -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DLLVM_TARGETS_TO_BUILD="X86" \
    -DLLVM_ENABLE_PROJECTS="clang;clang-tools-extra;lld" \
    -DLLVM_BUILD_LLVM_DYLIB=ON \
    -DLLVM_LINK_LLVM_DYLIB=ON \
    -DCLANG_LINK_CLANG_DYLIB=ON \
    -DLLVM_INSTALL_UTILS=ON \
    -DCMAKE_INSTALL_PREFIX=/opt/llvm \
    && ninja -C /tmp/llvm-build -j$(nproc) install \
    && cp --update=none /tmp/llvm-build/bin/* /opt/llvm/bin/ \
    && test -x /opt/llvm/bin/clangd-indexer

# ---------------- Main image ----------------
FROM ubuntu:24.04

# Arguments for creating user
ARG USERNAME=devuser
ARG UID=1000
ARG GID=1000
# CodeQL CLI version to install. Newer versions should work by just bumping this.
ARG CODEQL_VERSION=2.23.0

# Common environment settings
ENV LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8 \
    PATH=/home/${USERNAME}/.local/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Base tools and enable universe repository
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl sudo tzdata locales software-properties-common && \
    add-apt-repository -y universe && \
    rm -rf /var/lib/apt/lists/*

# Dev toolchain and Python 3.12
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    git make build-essential \
    python3 python3-pip python3-venv python3-dev \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev liblzma-dev xz-utils llvm \
    libncurses-dev libncurses5-dev libncursesw5-dev \
    bison flex libelf-dev bc gdb \
    debootstrap cmake wget libmpfr-dev libdw-dev \
    qemu-system-x86 qemu-utils qemu-kvm \
    libvirt-daemon-system libvirt-clients bridge-utils \
    jq unzip cscope codequery \
    && rm -rf /var/lib/apt/lists/*

# -------- NEW: bring in the freshly built LLVM/Clang/clangd-indexer --------
COPY --from=llvm-build /opt/llvm /opt/llvm
# Make sure the loader can find the fresh shared libraries
RUN echo "/opt/llvm/lib" > /etc/ld.so.conf.d/llvm-15.conf && ldconfig

# Fix for libmpfr version mismatch
RUN if [ ! -f /usr/lib/x86_64-linux-gnu/libmpfr.so.4 ]; then \
    ln -s /usr/lib/x86_64-linux-gnu/libmpfr.so /usr/lib/x86_64-linux-gnu/libmpfr.so.4; \
    fi

# Generate locale
RUN echo "en_US.UTF-8 UTF-8" >> /etc/locale.gen && \
    locale-gen en_US.UTF-8 && \
    update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# Install uv (does not use system pip)
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && uv --version

# ---------- Install CodeQL CLI ----------
# Layout: /opt/codeql/codeql, /opt/codeql/**.qlpack, etc.
RUN mkdir -p /opt && \
    cd /opt && \
    curl -fL --retry 5 --connect-timeout 20 \
    https://github.com/github/codeql-cli-binaries/releases/download/v${CODEQL_VERSION}/codeql-linux64.zip \
    -o codeql.zip && \
    unzip codeql.zip && rm -f codeql.zip && \
    /opt/codeql/codeql --version && \
    ln -s /opt/codeql/codeql /usr/bin/codeql

# Create non-root user and grant privileges
RUN groupadd -g ${GID} ${USERNAME} && \
    useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USERNAME} && \
    usermod -a -G kvm,libvirt ${USERNAME} || true && \
    echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Add ~/.local/bin and /opt/llvm/bin to PATH in interactive shells too
RUN echo 'export PATH="$HOME/.local/bin:$PATH"' >> /home/${USERNAME}/.bashrc && \
    echo 'export PATH="/opt/llvm/bin:$PATH"' >> /home/${USERNAME}/.bashrc

USER ${USERNAME}
WORKDIR /home/${USERNAME}

# Create a Python 3.12 virtual environment with uv and verify tools inside the venv
RUN uv venv --python 3.12 && \
    . .venv/bin/activate && \
    python -V && \
    pip -V

# Default command
CMD ["/bin/bash"]