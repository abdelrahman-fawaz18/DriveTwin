ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /worker
COPY src/evdt/__init__.py /usr/local/lib/python3.12/site-packages/evdt/__init__.py
COPY src/evdt/energy/__init__.py /usr/local/lib/python3.12/site-packages/evdt/energy/__init__.py
# Numerical records and calculation only. No source adapter, database client,
# controller, terrain downloads, credentials, or evaluation outcomes are packaged.
COPY src/evdt/energy/route_aware/__init__.py src/evdt/energy/route_aware/validation.py src/evdt/energy/route_aware/contracts.py src/evdt/energy/route_aware/policy.py src/evdt/energy/route_aware/wire.py src/evdt/energy/route_aware/scenarios.py src/evdt/energy/route_aware/environment.py src/evdt/energy/route_aware/geometry.py src/evdt/energy/route_aware/motion.py src/evdt/energy/route_aware/ledger.py src/evdt/energy/route_aware/forecast.py src/evdt/energy/route_aware/worker.py /usr/local/lib/python3.12/site-packages/evdt/energy/route_aware/
USER 10001:10001
COPY src/evdt/energy/route_aware/power.py /usr/local/lib/python3.12/site-packages/evdt/energy/route_aware/power.py
COPY src/evdt/energy/route_aware/signals.py /usr/local/lib/python3.12/site-packages/evdt/energy/route_aware/signals.py
COPY src/evdt/energy/moving_charge_worker.py src/evdt/energy/moving_charge_candidate.py src/evdt/energy/moving_charge_search.py src/evdt/energy/moving_charge_leg.py src/evdt/energy/moving_charge_station.py src/evdt/energy/moving_charge_transfer.py /usr/local/lib/python3.12/site-packages/evdt/energy/
ENTRYPOINT ["python", "-I", "-B", "-m", "evdt.energy.route_aware.worker"]
