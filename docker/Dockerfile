# DBScale CLI image.
#
#   docker build -t dbscale -f docker/Dockerfile .
#   docker run --rm -it \
#     -v /var/run/docker.sock:/var/run/docker.sock \   # lets DBScale start its sandbox container
#     -v "$PWD":/work -e DATABASE_URL=... dbscale run
#
# The sandbox PostgreSQL runs as a sibling container on the host's Docker daemon,
# which is why the socket is mounted. Alternatively, point `sandbox.type: url` at
# any empty PostgreSQL database and no Docker access is needed inside the image.
FROM python:3.11-slim AS build

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /install /usr/local
WORKDIR /work
ENTRYPOINT ["dbscale"]
CMD ["--help"]
