"""Wait for the isolated Compose stack, then run service tests with coverage."""

import asyncio
import os
import sys
import unittest

import asyncpg
import grpc
import redis.asyncio as redis
from coverage import Coverage
from redis.exceptions import RedisError


async def wait_for_services() -> None:
    targets = (
        os.environ.get("COLLABORATION_TARGET", "collaboration:50051"),
        os.environ.get("LLM_TARGET", "llm:50052"),
    )
    deadline = asyncio.get_running_loop().time() + 60
    while True:
        channels = [grpc.aio.insecure_channel(target) for target in targets]
        client = redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
        connection = None
        try:
            connection = await asyncpg.connect(
                host=os.environ.get("POSTGRES_HOST", "postgres"),
                user=os.environ["POSTGRES_USER"],
                password=os.environ["POSTGRES_PASSWORD"],
                database=os.environ["POSTGRES_DB"],
                timeout=2,
            )
            await asyncio.wait_for(client.ping(), timeout=2)
            await asyncio.gather(
                *(asyncio.wait_for(channel.channel_ready(), timeout=2) for channel in channels)
            )
            return
        except (OSError, TimeoutError, asyncpg.PostgresError, grpc.RpcError, RedisError):
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError("The test services did not become ready within 60 seconds.")
            await asyncio.sleep(1)
        finally:
            if connection is not None:
                await connection.close()
            await client.aclose()
            await asyncio.gather(*(channel.close() for channel in channels))


def main() -> int:
    asyncio.run(wait_for_services())
    coverage = Coverage(
        source=["services"],
        branch=True,
        data_file="/app/coverage/.coverage",
        omit=["*/services/tests/*", "*/services/proto/*"],
    )
    coverage.start()
    suite = unittest.defaultTestLoader.discover("services/tests", pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    coverage.stop()
    coverage.save()
    coverage.xml_report(outfile="/app/coverage/python.xml")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
