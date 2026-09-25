#!/bin/sh
# Runs before every container start (local Docker Compose AND any cloud
# platform running this same image, e.g. Cloud Run): ensures the model
# artifact is present (see docker_model_fetch.py's docstring for why this
# is a no-op locally and a one-time download in the cloud), then execs
# whatever command was actually requested -- the Dockerfile's own CMD in
# the cloud, or docker-compose.yml's `command:` override (with --reload)
# for local development. Never changes what that command is.
set -e

python3 docker_model_fetch.py

exec "$@"
