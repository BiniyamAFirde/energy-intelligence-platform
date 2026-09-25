FROM python:3.11-slim

# Runs as root (unlike frontend/Dockerfile's non-root `node` user) --
# deliberate, not an oversight: this container writes into host bind
# mounts (models/, reports/, data/processed/) from the forecasting/
# anomaly CLI jobs (train.py, evaluate.py, detect.py, plots.py). A non-
# root container user's uid generally won't match the host user's uid
# under Docker Desktop, which would turn those writes into permission
# errors and break the "regenerate everything from source" reproducibility
# story this project is built around. Revisit if/when those jobs stop
# needing to write to host-mounted paths.
WORKDIR /app

COPY pyproject.toml .
COPY src ./src
COPY tests ./tests
RUN pip install --no-cache-dir -e ".[dev]"

COPY docker_model_fetch.py docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
# Shell form (not exec/JSON form) deliberately -- ${PORT:-8000} only
# expands because this runs through a shell. Cloud Run (like most
# container platforms, e.g. Render) sets PORT and expects the container
# to listen on it; local docker-compose.yml overrides this whole CMD with
# its own literal `--port 8000 --reload` (untouched by this change,
# container-internal port 8000 there always matches its "8000:8000" port
# mapping), so this default only matters when the platform runs the image
# directly. See docs/deployment_cloud_run.md.
CMD ["sh", "-c", "uvicorn energy_platform.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
