# Lambda Cloud's default hosts provide the NVIDIA driver and container toolkit.
# Match the explicitly locked CUDA 12.8 wheels and Lambda's R570 drivers.
FROM nvidia/cuda:12.8.1-base-ubuntu24.04

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
COPY lambda/gpu_joint.py ./lambda/gpu_joint.py

RUN uv sync --frozen --no-dev --group imagery --group train

ENTRYPOINT ["uv", "run", "--frozen", "--no-sync", "urban-tree-ml"]
CMD ["--help"]
