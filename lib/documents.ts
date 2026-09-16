import type { JSONContent } from "@tiptap/core";

export type DocumentViewModel = {
  id: string;
  title: string;
  updatedLabel: string;
  content: JSONContent;
};

export const blankDocumentContent: JSONContent = {
  type: "doc",
  content: [{ type: "paragraph" }],
};

export const sampleDocuments: DocumentViewModel[] = [
  {
    id: "distributed-systems-design",
    title: "Distributed systems design",
    updatedLabel: "Opened today",
    content: {
      type: "doc",
      content: [
        {
          type: "heading",
          attrs: { level: 1 },
          content: [{ type: "text", text: "Distributed Collaboration Platform" }],
        },
        {
          type: "paragraph",
          content: [
            {
              type: "text",
              text: "A working draft for the real-time document collaboration architecture.",
            },
          ],
        },
        {
          type: "heading",
          attrs: { level: 2 },
          content: [{ type: "text", text: "Milestone 1" }],
        },
        {
          type: "bulletList",
          content: [
            {
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: "Authenticated document access" }],
                },
              ],
            },
            {
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: "Real-time update propagation" }],
                },
              ],
            },
            {
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: "Context-aware writing assistance" }],
                },
              ],
            },
          ],
        },
      ],
    },
  },
  {
    id: "project-kickoff-notes",
    title: "Project kickoff notes",
    updatedLabel: "Opened yesterday",
    content: {
      type: "doc",
      content: [
        {
          type: "heading",
          attrs: { level: 1 },
          content: [{ type: "text", text: "Project Kickoff" }],
        },
        {
          type: "paragraph",
          content: [
            { type: "text", marks: [{ type: "bold" }], text: "Goal: " },
            {
              type: "text",
              text: "Deliver a resilient multi-user editor with clear service boundaries.",
            },
          ],
        },
        {
          type: "heading",
          attrs: { level: 2 },
          content: [{ type: "text", text: "Next steps" }],
        },
        {
          type: "orderedList",
          content: [
            {
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: "Confirm service responsibilities" }],
                },
              ],
            },
            {
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: "Define protobuf contracts" }],
                },
              ],
            },
          ],
        },
      ],
    },
  },
  {
    id: "weekly-team-sync",
    title: "Weekly team sync",
    updatedLabel: "Opened 4 days ago",
    content: {
      type: "doc",
      content: [
        {
          type: "heading",
          attrs: { level: 1 },
          content: [{ type: "text", text: "Weekly Team Sync" }],
        },
        {
          type: "paragraph",
          content: [{ type: "text", text: "Agenda" }],
        },
        {
          type: "bulletList",
          content: [
            {
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: "Frontend progress" }],
                },
              ],
            },
            {
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: "gRPC service planning" }],
                },
              ],
            },
            {
              type: "listItem",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: "Demo responsibilities" }],
                },
              ],
            },
          ],
        },
      ],
    },
  },
];

/** Finds a local sample document without defining a backend persistence contract. */
export function findSampleDocument(documentId: string) {
  return sampleDocuments.find((document) => document.id === documentId);
}
