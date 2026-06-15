FROM python:3.12-slim

WORKDIR /worker

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY worker.py .
COPY script_runner.py .
COPY topology_mapper.py .

CMD ["python", "worker.py"]
