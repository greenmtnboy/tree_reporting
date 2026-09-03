# Lambda Cloud's default hosts provide the NVIDIA driver and container toolkit.
# PyTorch 2.13's Linux wheel carries CUDA 13 user-space libraries; this base
# supplies the matching CUDA generation without duplicating a second cuDNN.
FROM nvidia/cuda:13.0.2-base-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.10.10 /uv /uvx /bin/

WORKDIR /workspace/imagery_model
COPY .python-version pyproject.toml uv.lock ./

# Keep the large PyTorch/CUDA dependency layer reusable when only application
# source, configs, or documentation change.
RUN uv sync --frozen --no-dev --group imagery --group train --no-install-project

COPY README.md ./
COPY src ./src
COPY configs ./configs

RUN uv sync --frozen --no-dev --group imagery --group train

ENTRYPOINT ["uv", "run", "--frozen", "--no-sync", "urban-tree-ml"]
CMD ["--help"]
