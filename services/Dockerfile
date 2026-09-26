FROM python:3.11-slim

WORKDIR /app
COPY services/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY services /app/services
RUN python -m grpc_tools.protoc -I . --python_out=. --grpc_python_out=. services/proto/collaboration.proto

ENV PYTHONPATH=/app
