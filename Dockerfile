# syntax=docker/dockerfile:1.7
#
# diagnostic.ar question agent: POST /v1/rounds and GET /healthz on port 8010.
#
#   docker build -t diagnosticar-question-agent:dev .
#
# The knowledge base is rebuilt here from the pinned HPO release (every source
# verified by sha256), so a laptop build and a CI build ship the same file.
# The platform's release job tests the image by mounting tests/ and docs/ (the
# contract example) read-only and installing pytest for that run only:
#
#   docker run --rm -e HOME=/tmp -v "$PWD/tests:/tmp/tests:ro" -v "$PWD/docs:/tmp/docs:ro" IMAGE \
#     sh -c "pip install -q --user pytest==8.3.2 && python -m pytest /tmp/tests -q -p no:cacheprovider -m 'not live'"
#
# That is why the runtime keeps pip and installs into the system interpreter
# rather than a virtualenv: `pip install --user` refuses to run inside one.

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
# pyproject.toml only sets lower bounds; the constraints pin every runtime
# dependency, so two builds of one commit install the same versions.
RUN pip wheel --wheel-dir /wheels -c constraints.txt . \
    && pip install --no-index --find-links /wheels diagnosticar-preguntas

# The download directory is a cache mount: a code change rebuilds the knowledge
# base without fetching the ~80 MB of sources again. Files already there are
# reused only if their sha256 still matches the manifest.
RUN --mount=type=cache,target=/var/cache/kb-sources \
    diagnosticar-preguntas-kb --download --download-dir /var/cache/kb-sources --out /kb/kb.sqlite \
    && cat /kb/kb.sqlite.sha256


# The claude CLI version the agent was measured with; the claude-cli provider
# shells out to it. This version's npm package ships a self-contained native
# binary, so only the package leaves this stage: the runtime carries neither
# Node nor npm (whose bundled dependencies carry HIGH advisories the release
# scan rejects; see the platform's CI_CD.md, "Why the Dockerfiles delete npm").
# Without npm the CLI cannot update itself, which is what a pinned version wants.
FROM node:22-bookworm-slim AS claude-cli
RUN npm install -g @anthropic-ai/claude-code@2.1.270 \
    && /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe --version


FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DISABLE_AUTOUPDATER=1

# The published base image lags on Debian security fixes.
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

COPY --from=claude-cli /usr/local/lib/node_modules/@anthropic-ai/claude-code /opt/claude-code
RUN ln -s /opt/claude-code/bin/claude.exe /usr/local/bin/claude

RUN useradd --create-home --uid 10001 app

RUN --mount=type=bind,from=builder,source=/wheels,target=/tmp/wheels \
    pip install --no-index --find-links /tmp/wheels diagnosticar-preguntas

# Owned by root and read-only: the service opens it with mode=ro and never writes.
RUN mkdir -p /app/data
COPY --from=builder --chmod=0444 /kb/kb.sqlite /kb/kb.sqlite.sha256 /app/data/
WORKDIR /app

ENV HOME=/home/app \
    PREGUNTAS_KB_PATH=/app/data/kb.sqlite \
    PREGUNTAS_HOST=0.0.0.0 \
    PREGUNTAS_PORT=8010

USER app
EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PREGUNTAS_PORT\", \"8010\")}/healthz', timeout=4)"

CMD ["python", "-m", "diagnosticar_preguntas.api"]
