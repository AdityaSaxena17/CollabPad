"use client";

import { useAuth } from "@clerk/nextjs";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import * as Y from "yjs";
import { DocumentEditor } from "@/components/document-editor";
import {
  gatewayWebSocketUrl,
  getDocument,
  setDocumentSharing,
  type DocumentRecord,
} from "@/lib/gateway";

type PendingEvent =
  | { type: "edit"; id: string; update: string }
  | { type: "rename"; id: string; title: string };

type ConnectionStatus = "connecting" | "connected" | "disconnected" | "denied";

function encodeUpdate(update: Uint8Array): string {
  return btoa(Array.from(update, (byte) => String.fromCharCode(byte)).join(""));
}

function decodeUpdate(encoded: string): Uint8Array {
  return Uint8Array.from(atob(encoded), (character) => character.charCodeAt(0));
}

/** Loads a real document, reconciles Yjs updates, and tracks durable acknowledgements. */
export function CollaborativeDocument({ documentId }: { documentId: string }) {
  return <DocumentSession key={documentId} documentId={documentId} />;
}

function DocumentSession({ documentId }: { documentId: string }) {
  const { getToken, isLoaded, userId } = useAuth();
  const ydoc = useMemo(() => new Y.Doc(), []);
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [loadError, setLoadError] = useState("");
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [pendingCount, setPendingCount] = useState(0);
  const socketRef = useRef<WebSocket | null>(null);
  const snapshotReceived = useRef(false);
  const pending = useRef(new Map<string, PendingEvent>());
  const denied = useRef(false);

  useEffect(() => {
    if (!isLoaded) return;
    let cancelled = false;

    async function load() {
      try {
        const token = await getToken();
        if (!token) throw new Error("Sign in to open this document.");
        const saved = await getDocument(documentId, token);
        if (!cancelled) setDocument(saved);
      } catch (cause) {
        if (!cancelled) {
          setLoadError(cause instanceof Error ? cause.message : "Could not open document.");
        }
      }
    }

    void load();
    return () => { cancelled = true; };
  }, [documentId, getToken, isLoaded]);

  useEffect(() => {
    function onUpdate(update: Uint8Array, origin: unknown) {
      if (origin === "remote") return;
      const event: PendingEvent = {
        type: "edit",
        id: crypto.randomUUID(),
        update: encodeUpdate(update),
      };
      pending.current.set(event.id, event);
      setPendingCount(pending.current.size);
      const socket = socketRef.current;
      if (socket?.readyState === WebSocket.OPEN && snapshotReceived.current) {
        socket.send(JSON.stringify(event));
      }
    }

    ydoc.on("update", onUpdate);
    return () => { ydoc.off("update", onUpdate); };
  }, [ydoc]);

  const documentLoaded = document !== null;
  useEffect(() => {
    if (!documentLoaded || !isLoaded || !userId) return;
    let disposed = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;

    async function connect() {
      if (disposed || denied.current) return;
      try {
        const token = await getToken();
        if (!token || disposed) return;
        const socket = new WebSocket(
          `${gatewayWebSocketUrl}/ws/documents/${encodeURIComponent(documentId)}`,
        );
        socketRef.current = socket;
        snapshotReceived.current = false;

        socket.onopen = () => {
          socket.send(JSON.stringify({ type: "auth", token }));
        };

        socket.onmessage = (event) => {
          try {
            const message: unknown = JSON.parse(event.data);
            if (typeof message !== "object" || message === null || !("type" in message)) {
              throw new Error("Invalid synchronization message.");
            }

            if (message.type === "authenticated") {
              if (refreshTimer) clearTimeout(refreshTimer);
              refreshTimer = setTimeout(() => {
                // Refreshing the short-lived Clerk token keeps the open socket authorized.
                void getToken({ skipCache: true }).then((freshToken) => {
                  if (freshToken && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ type: "auth", token: freshToken }));
                  } else {
                    socket.close();
                  }
                }).catch(() => socket.close());
              }, 40_000);
            } else if (message.type === "snapshot" &&
              "update" in message && typeof message.update === "string" &&
              "title" in message && typeof message.title === "string") {
              Y.applyUpdate(ydoc, decodeUpdate(message.update), "remote");
              const savedTitle = message.title;
              setDocument((current) => current ? { ...current, title: savedTitle } : current);
              snapshotReceived.current = true;
              setReady(true);
              setStatus("connected");
              for (const unsaved of pending.current.values()) {
                socket.send(JSON.stringify(unsaved));
              }
            } else if (message.type === "edit" &&
              "update" in message && typeof message.update === "string") {
              Y.applyUpdate(ydoc, decodeUpdate(message.update), "remote");
              if ("id" in message && typeof message.id === "string") {
                pending.current.delete(message.id);
                setPendingCount(pending.current.size);
              }
            } else if (message.type === "ack" &&
              "id" in message && typeof message.id === "string") {
              pending.current.delete(message.id);
              setPendingCount(pending.current.size);
            } else if (message.type === "title" &&
              "title" in message && typeof message.title === "string") {
              const savedTitle = message.title;
              setDocument((current) => current ? { ...current, title: savedTitle } : current);
              if ("id" in message && typeof message.id === "string") {
                pending.current.delete(message.id);
                setPendingCount(pending.current.size);
              }
            } else if (message.type === "access_revoked" || message.type === "access_denied") {
              denied.current = true;
              setStatus("denied");
              socket.close();
            } else if (message.type === "service_unavailable") {
              socket.close();
            } else {
              throw new Error("Unexpected synchronization message.");
            }
          } catch {
            socket.close();
          }
        };

        socket.onclose = () => {
          if (refreshTimer) clearTimeout(refreshTimer);
          if (socketRef.current === socket) socketRef.current = null;
          snapshotReceived.current = false;
          if (!disposed && !denied.current) {
            setStatus("disconnected");
            retryTimer = setTimeout(() => { void connect(); }, 1_500);
          }
        };
      } catch {
        if (!disposed && !denied.current) {
          setStatus("disconnected");
          retryTimer = setTimeout(() => { void connect(); }, 1_500);
        }
      }
    }

    void connect();
    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (refreshTimer) clearTimeout(refreshTimer);
      socketRef.current?.close();
      socketRef.current = null;
    };
  }, [documentId, documentLoaded, getToken, isLoaded, userId, ydoc]);

  function rename(title: string) {
    const event: PendingEvent = { type: "rename", id: crypto.randomUUID(), title };
    pending.current.set(event.id, event);
    setPendingCount(pending.current.size);
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN && snapshotReceived.current) {
      socket.send(JSON.stringify(event));
    }
  }

  async function setSharing(enabled: boolean) {
    const token = await getToken();
    if (!token) throw new Error("Sign in to change sharing.");
    const updated = await setDocumentSharing(documentId, enabled, token);
    setDocument(updated);
  }

  if (loadError || status === "denied") {
    return (
      <main className="grid min-h-screen place-items-center bg-[#f8fafd] px-4 text-[#202124]">
        <div className="max-w-md rounded-xl border border-[#dadce0] bg-white p-8 text-center">
          <h1 className="text-xl font-medium">Document unavailable</h1>
          <p role="alert" className="mt-3 text-sm text-[#5f6368]">{loadError || "Access to this document was revoked."}</p>
          <Link href="/documents" className="mt-5 inline-block text-sm text-[#1967d2]">Back to documents</Link>
        </div>
      </main>
    );
  }

  if (!document || !ready) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#f8fafd] text-[#5f6368]">
        <p role="status">{status === "disconnected" ? "Reconnecting to the document…" : "Opening document…"}</p>
      </main>
    );
  }

  return (
    <DocumentEditor
      document={document}
      sharedDocument={ydoc}
      status={status}
      pendingCount={pendingCount}
      isOwner={document.ownerId === userId}
      onRename={rename}
      onSetSharing={setSharing}
    />
  );
}
