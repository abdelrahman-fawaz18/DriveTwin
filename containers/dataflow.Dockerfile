ARG UV_IMAGE
ARG PYTHON_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS tools
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src UV_PYTHON_DOWNLOADS=never
WORKDIR /app
FROM tools AS runtime
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
COPY src ./src
COPY contracts ./contracts
COPY catalog ./catalog
COPY migrations ./migrations
USER 10001:10001
CMD ["python", "-m", "evdt.api"]

FROM runtime AS checks
USER root
RUN uv sync --locked --no-install-project
COPY tests/unit ./tests/unit
COPY scripts/privacy_guard.py ./scripts/
COPY scripts/platform/dataflow.py scripts/platform/lock_dataflow_images.py ./scripts/platform/
USER 10001:10001
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
