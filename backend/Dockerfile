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

EXPOSE 8000

CMD ["uvicorn", "energy_platform.main:app", "--host", "0.0.0.0", "--port", "8000"]
