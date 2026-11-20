ARG NODE_IMAGE
ARG UV_IMAGE
ARG PYTHON_IMAGE
FROM ${NODE_IMAGE} AS node
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE}
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
COPY packages/contracts-ts/package.json packages/contracts-ts/package-lock.json ./packages/contracts-ts/
RUN cd packages/contracts-ts && node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --no-audit --no-fund
COPY packages/contracts-ts/src ./packages/contracts-ts/src
COPY packages/contracts-ts/tsconfig.json ./packages/contracts-ts/
COPY apps/dashboard/package.json apps/dashboard/package-lock.json ./apps/dashboard/
RUN cd apps/dashboard && node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --no-audit --no-fund
COPY apps/dashboard ./apps/dashboard
COPY apps/launcher ./apps/launcher
COPY docs/media/charging-story/*.png ./docs/media/charging-story/
COPY docs/media/dashboard/overview.png ./docs/media/dashboard/overview.png
COPY contracts/monitoring.profile.json ./contracts/monitoring.profile.json
COPY catalog/monitoring-signals.json ./catalog/monitoring-signals.json
COPY data/public/montreal-core-v1/roads.osm.xml ./data/public/montreal-core-v1/roads.osm.xml
COPY data/public/montreal-backdrop-v1 ./data/public/montreal-backdrop-v1
RUN cd apps/dashboard && node /usr/local/lib/node_modules/npm/bin/npm-cli.js run format:check && node /usr/local/lib/node_modules/npm/bin/npm-cli.js run build
RUN node packages/contracts-ts/node_modules/typescript/bin/tsc -p packages/contracts-ts/tsconfig.json
COPY src ./src
COPY contracts ./contracts
COPY catalog ./catalog
COPY migrations ./migrations
COPY scripts ./scripts
COPY containers/energy-route-worker.Dockerfile ./containers/
COPY environments/dataflow-images.json ./environments/
COPY tests/unit ./tests/unit
COPY tests/foundation ./tests/foundation
COPY tests/monitoring ./tests/monitoring
COPY benchmarks ./benchmarks
COPY simulation/montreal/plant.json ./simulation/montreal/plant.json
COPY simulation/montreal/mission.json ./simulation/montreal/mission.json
COPY simulation/montreal/bus-parameters.json simulation/montreal/import-review.json simulation/montreal/stations.json ./simulation/montreal/
COPY simulation/montreal-sustained ./simulation/montreal-sustained
COPY simulation/montreal-bus-002 ./simulation/montreal-bus-002
COPY simulation/geographic ./simulation/geographic
COPY simulation/montreal-history ./simulation/montreal-history
COPY qualification/extension ./qualification/extension
COPY qualification/workload ./qualification/workload
COPY qualification/energy ./qualification/energy
COPY qualification/contracts ./qualification/contracts
COPY qualification/monitoring ./qualification/monitoring
COPY qualification/dashboard ./qualification/dashboard
USER 10001:10001
CMD ["python", "scripts/checks.py"]
