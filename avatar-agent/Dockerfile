# Containerized LiveKit agent worker. One worker process hosts one (or a few)
# concurrent meeting sessions; scale horizontally by running more replicas.
FROM python:3.11-slim

# Silero VAD / turn-detector models are downloaded at build time so cold starts
# don't pay for the fetch on the first meeting.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -e . \
    && python -m avatar_agent.agent download-files

# The worker connects out to LiveKit Cloud; it does not need an inbound port.
ENTRYPOINT ["avatar-agent"]
CMD ["start"]
