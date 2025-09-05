FROM ubuntu:24.04

# Arguments for creating user
ARG USERNAME=devuser
ARG UID=1000
ARG GID=1000

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
      clang-15 lld-15 llvm-15 llvm-15-dev libclang-15-dev libc++-15-dev libc++abi-15-dev \
      jq \
    && rm -rf /var/lib/apt/lists/*

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

# Create non-root user and grant privileges
RUN groupadd -g ${GID} ${USERNAME} && \
    useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USERNAME} && \
    usermod -a -G kvm,libvirt ${USERNAME} || true && \
    echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Add ~/.local/bin to PATH in interactive shells too
RUN echo 'export PATH="$HOME/.local/bin:$PATH"' >> /home/${USERNAME}/.bashrc

USER ${USERNAME}
WORKDIR /home/${USERNAME}

# Create a Python 3.12 virtual environment with uv and verify tools inside the venv
RUN uv venv --python 3.12 && \
    . .venv/bin/activate && \
    python -V && \
    pip -V

# Default command
CMD ["/bin/bash"]