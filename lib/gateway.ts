export type DocumentRecord = {
  id: string;
  title: string;
  ownerId: string;
  shareEnabled: boolean;
  updatedAt: string;
};

export class GatewayError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const baseUrl = process.env.NEXT_PUBLIC_GATEWAY_HTTP_URL ?? "http://localhost:18080";
export const gatewayWebSocketUrl =
  process.env.NEXT_PUBLIC_GATEWAY_WS_URL ?? "ws://localhost:18080";

function isDocumentRecord(value: unknown): value is DocumentRecord {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  return (
    "id" in value && typeof value.id === "string" &&
    "title" in value && typeof value.title === "string" &&
    "ownerId" in value && typeof value.ownerId === "string" &&
    "shareEnabled" in value && typeof value.shareEnabled === "boolean" &&
    "updatedAt" in value && typeof value.updatedAt === "string"
  );
}

async function request(path: string, token: string, init?: RequestInit): Promise<unknown> {
  let response: Response;

  try {
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
      },
      cache: "no-store",
    });
  } catch {
    throw new GatewayError("Cannot reach the collaboration gateway.", 503);
  }

  if (!response.ok) {
    throw new GatewayError(
      response.status === 404
        ? "Document not found or you do not have access."
        : response.status === 401
          ? "Your session has expired. Sign in again."
          : "The collaboration service is unavailable.",
      response.status,
    );
  }

  return response.json();
}

/** Loads the authenticated user's saved documents from the Python gateway. */
export async function listDocuments(token: string): Promise<DocumentRecord[]> {
  const result = await request("/api/documents", token);
  if (
    typeof result !== "object" || result === null ||
    !("documents" in result) || !Array.isArray(result.documents) ||
    !result.documents.every(isDocumentRecord)
  ) {
    throw new GatewayError("The gateway returned an invalid document list.", 502);
  }
  return result.documents;
}

/** Creates a persistent blank document owned by the signed-in user. */
export async function createDocument(token: string): Promise<DocumentRecord> {
  const result = await request("/api/documents", token, {
    method: "POST",
    body: JSON.stringify({ title: "Untitled document" }),
  });
  if (!isDocumentRecord(result)) {
    throw new GatewayError("The gateway returned an invalid document.", 502);
  }
  return result;
}

/** Loads metadata for a document the signed-in user may edit. */
export async function getDocument(
  documentId: string,
  token: string,
): Promise<DocumentRecord> {
  const result = await request(`/api/documents/${encodeURIComponent(documentId)}`, token);
  if (!isDocumentRecord(result)) {
    throw new GatewayError("The gateway returned an invalid document.", 502);
  }
  return result;
}

/** Enables or disables editing by authenticated users who have the document link. */
export async function setDocumentSharing(
  documentId: string,
  enabled: boolean,
  token: string,
): Promise<DocumentRecord> {
  const result = await request(
    `/api/documents/${encodeURIComponent(documentId)}/sharing`,
    token,
    { method: "PATCH", body: JSON.stringify({ enabled }) },
  );
  if (!isDocumentRecord(result)) {
    throw new GatewayError("The gateway returned an invalid document.", 502);
  }
  return result;
}

/** Requests a plain-text continuation at the current editor cursor. */
export async function completeText(
  documentId: string,
  prefix: string,
  suffix: string,
  token: string,
): Promise<string> {
  let result: unknown;
  try {
    result = await request(
      `/api/documents/${encodeURIComponent(documentId)}/ai/completion`,
      token,
      { method: "POST", body: JSON.stringify({ prefix, suffix }) },
    );
  } catch (cause) {
    if (cause instanceof GatewayError && [501, 503, 504].includes(cause.status)) {
      throw new GatewayError(
        cause.status === 504 ? "AI completion timed out. Try again." : "AI completion is unavailable. Try again later.",
        cause.status,
      );
    }
    throw cause;
  }
  if (
    typeof result !== "object" || result === null ||
    !("text" in result) || typeof result.text !== "string" ||
    (result.text !== "" && !result.text.trim()) || result.text.length > 600 ||
    [...result.text].some((character) => character.charCodeAt(0) < 32 && character !== "\n" && character !== "\t")
  ) {
    throw new GatewayError("The AI service returned an invalid suggestion.", 502);
  }
  return result.text;
}
