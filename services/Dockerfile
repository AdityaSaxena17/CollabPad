FROM python:3.11-slim

WORKDIR /app
COPY services/requirements.txt services/requirements-dev.txt /app/
ARG REQUIREMENTS_FILE=requirements.txt
RUN pip install --no-cache-dir -r "/app/${REQUIREMENTS_FILE}"
COPY services /app/services
RUN python -m grpc_tools.protoc -I . --python_out=. --grpc_python_out=. services/proto/collaboration.proto services/proto/llm.proto

ENV PYTHONPATH=/app
