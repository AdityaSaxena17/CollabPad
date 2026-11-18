"""Start the private LLM gRPC service."""

import asyncio
import logging
import os

import grpc

from services.llm.service import LlmServicer
from services.llm.completion import LlamaCompletionEngine
from services.llm.summary import LlamaSummaryEngine
from services.llm.enhancement import LlamaEnhancementEngine
from services.llm.scheduler import ModelScheduler
from services.proto import llm_pb2_grpc


async def local_engines(scheduler):
    model_path = os.environ.get("LLM_MODEL_PATH", "/models/model.gguf")
    engine = (
        await scheduler.run(LlamaCompletionEngine, model_path)
        if os.path.isfile(model_path) else None
    )
    if engine is None:
        logging.warning("LLM model not found at %s; AI tasks are unavailable.", model_path)
    summary_engine = (
        LlamaSummaryEngine(engine.model, int(os.environ.get("LLM_N_CTX", "4096")))
        if engine is not None else None
    )
    enhancement_engine = (
        LlamaEnhancementEngine(engine.model, int(os.environ.get("LLM_N_CTX", "4096")))
        if engine is not None else None
    )
    return engine, summary_engine, enhancement_engine


async def main():
    scheduler = ModelScheduler()
    provider = os.environ.get("LLM_PROVIDER", "local")
    if provider == "local":
        engine, summary_engine, enhancement_engine = await local_engines(scheduler)
    elif provider == "bedrock":
        from services.llm.bedrock import BedrockEngine

        engine = await scheduler.run(BedrockEngine.from_environment)
        summary_engine = enhancement_engine = engine
    else:
        raise ValueError("LLM_PROVIDER must be local or bedrock.")
    servicer = LlmServicer(engine, summary_engine, scheduler, enhancement_engine)
    server = grpc.aio.server(options=[("grpc.max_receive_message_length", 1_048_576)])
    llm_pb2_grpc.add_LlmServiceServicer_to_server(servicer, server)
    server.add_insecure_port("[::]:50052")
    await server.start()
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(grace=5)
        await servicer.close()
        if provider == "bedrock":
            engine.client.close()


if __name__ == "__main__":
    asyncio.run(main())
