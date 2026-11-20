ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
WORKDIR /worker
# Explicit packaging excludes the plant, dataset design, harness, credentials and
# general project scripts. No dependency installer or additional framework is used.
COPY src/evdt/__init__.py /usr/local/lib/python3.12/site-packages/evdt/__init__.py
COPY src/evdt/energy/__init__.py src/evdt/energy/model.py src/evdt/energy/observations.py src/evdt/energy/forecasts.py src/evdt/energy/jobs.py src/evdt/energy/worker.py /usr/local/lib/python3.12/site-packages/evdt/energy/
USER 10001:10001
ENTRYPOINT ["python", "-I", "-B", "-m", "evdt.energy.worker"]
