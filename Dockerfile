# Keelson in a container: the web console by default, the CLI with arguments.
#
#   docker compose up                        web console on http://localhost:8765
#   docker compose run --rm keelson --help   CLI
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KEELSON_HOME=/data

# git and ripgrep are what the built-in tools shell out to.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ripgrep \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY agents ./agents
# Read-only artifacts the console's benchmark, skill-evolution and trace pages show.
COPY bench ./bench
COPY docs/self-evolution-demo ./docs/self-evolution-demo
COPY demo ./demo

# Editable, so the package keeps resolving those artifacts relative to /app.
RUN pip install --no-cache-dir -e ".[web]"

# The agent works on whatever is mounted here.
WORKDIR /workspace
EXPOSE 8765

ENTRYPOINT ["keelson"]
# Inside a container the server has to listen on all interfaces to be
# reachable; publish the port to loopback only (see docker-compose.yml).
CMD ["web", "--host", "0.0.0.0", "--in-container", "--no-open"]
