import { Pool } from "pg";

let databasePool: Pool | undefined;

/** Returns the process-wide PostgreSQL pool, creating it lazily at request time. */
export function getDatabasePool() {
  if (databasePool) {
    return databasePool;
  }

  const connectionString = process.env.DATABASE_URL;

  if (!connectionString) {
    throw new Error("DATABASE_URL is not configured.");
  }

  databasePool = new Pool({
    connectionString,
    max: 10,
    connectionTimeoutMillis: 5_000,
    idleTimeoutMillis: 30_000,
  });

  databasePool.on("error", () => {
    console.error("An idle PostgreSQL connection failed unexpectedly.");
  });

  return databasePool;
}
