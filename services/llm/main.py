"""Start the private LLM gRPC service."""

import asyncio
import logging
import os

import grpc

from services.llm.service import LlmServicer
from services.llm.completion import LlamaCompletionEngine
from services.proto import llm_pb2_grpc


async def main():
    model_path = os.environ.get("LLM_MODEL_PATH", "/models/model.gguf")
    engine = LlamaCompletionEngine(model_path) if os.path.isfile(model_path) else None
    if engine is None:
        logging.warning("LLM model not found at %s; completion is unavailable.", model_path)
    server = grpc.aio.server(options=[("grpc.max_receive_message_length", 1_048_576)])
    llm_pb2_grpc.add_LlmServiceServicer_to_server(LlmServicer(engine), server)
    server.add_insecure_port("[::]:50052")
    await server.start()
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=5)


if __name__ == "__main__":
    asyncio.run(main())
