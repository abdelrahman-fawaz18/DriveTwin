ARG UV_IMAGE
ARG PYTHON_IMAGE
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS tools
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
FROM tools AS simulation
COPY containers/sumo-debian.sources /etc/apt/sources.list.d/debian.sources
RUN ["apt-get", "update"]
RUN ["apt-get", "install", "-y", "--no-install-recommends", "libx11-6", "libxext6", "libxrender1", "libgl1", "libatomic1", "libexpat1"]
RUN python -c "import subprocess,pathlib; pathlib.Path('/app/debian-packages.tsv').write_text(subprocess.check_output(['dpkg-query','-W'],text=True))"
COPY simulation/reference/pyproject.toml simulation/reference/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
COPY simulation/reference ./scenario
COPY scripts/platform/sumo_probe.py ./sumo_probe.py
USER 10001:10001
ENTRYPOINT ["python", "/app/sumo_probe.py"]
