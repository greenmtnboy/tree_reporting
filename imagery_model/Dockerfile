# Lambda Cloud's default hosts provide the NVIDIA driver and container toolkit.
# Match the explicitly locked CUDA 12.8 wheels and Lambda's R570 drivers.
FROM rust:1.91.1-slim-bookworm AS rust-toolchain
FROM nvidia/cuda:12.8.1-base-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git libgdal-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /uvx /bin/
COPY --from=rust-toolchain /usr/local/cargo /usr/local/cargo
COPY --from=rust-toolchain /usr/local/rustup /usr/local/rustup

WORKDIR /workspace/imagery_model
COPY .python-version pyproject.toml uv.lock ./

# Keep the large PyTorch/CUDA dependency layer reusable when only application
# source, configs, or documentation change.
RUN uv sync --frozen --no-dev --group imagery --group train --no-install-project

COPY README.md ./
COPY src ./src
COPY native/build_review_native.py ./native/build_review_native.py
COPY native/review_native/Cargo.toml native/review_native/Cargo.lock ./native/review_native/
COPY native/review_native/src ./native/review_native/src
COPY configs ./configs
COPY lambda/gpu_joint.py ./lambda/gpu_joint.py

RUN uv sync --frozen --no-dev --group imagery --group train
RUN uv run --frozen --no-sync python native/build_review_native.py \
    && uv run --frozen --no-sync python -c "from urban_tree_ml.review_native import backend_info; assert backend_info()['backend'] == 'rust'"

ENTRYPOINT ["uv", "run", "--frozen", "--no-sync", "urban-tree-ml"]
CMD ["--help"]
