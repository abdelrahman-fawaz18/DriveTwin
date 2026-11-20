ARG UV_IMAGE
ARG PYTHON_IMAGE
ARG NODE_IMAGE
FROM ${NODE_IMAGE} AS dashboard
WORKDIR /app/apps/dashboard
COPY contracts/monitoring.profile.json /app/contracts/monitoring.profile.json
COPY apps/dashboard/package.json apps/dashboard/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY apps/dashboard .
COPY catalog/monitoring-signals.json /app/catalog/monitoring-signals.json
COPY data/public/montreal-core-v1/roads.osm.xml /app/data/public/montreal-core-v1/roads.osm.xml
COPY data/public/montreal-backdrop-v1 /app/data/public/montreal-backdrop-v1
RUN npm run build
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
FROM dependencies AS runtime
COPY qualification/energy/product-profile.json ./qualification/energy/product-profile.json
COPY qualification/energy/product-profile-1.1.0.json qualification/energy/station-navigation.json ./qualification/energy/
COPY --from=dashboard /app/apps/dashboard/dist ./dashboard
COPY src ./src
COPY contracts ./contracts
COPY catalog ./catalog
COPY migrations ./migrations
RUN mkdir /spool /source-output /measurements && chown 10001:10001 /spool /source-output /measurements
USER 10001:10001
CMD ["python", "-m", "evdt.monitoring.ingest"]

FROM dependencies AS simulator
COPY containers/sumo-debian.sources /etc/apt/sources.list.d/debian.sources
RUN apt-get update && apt-get install -y --no-install-recommends libx11-6 libxext6 libxrender1 libgl1 libatomic1 libexpat1
RUN uv sync --locked --no-dev --extra simulator --no-install-project
COPY src ./src
COPY contracts ./contracts
COPY catalog ./catalog
COPY migrations ./migrations
COPY scripts/monitoring/verify_plant.py ./scripts/monitoring/verify_plant.py
RUN mkdir /spool /source-output /measurements && chown 10001:10001 /spool /source-output /measurements
USER 10001:10001
CMD ["python", "-m", "evdt.monitoring.adapter"]
