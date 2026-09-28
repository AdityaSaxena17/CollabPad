"use client";

import { UserButton } from "@clerk/nextjs";
import Collaboration from "@tiptap/extension-collaboration";
import CollaborationCaret from "@tiptap/extension-collaboration-caret";
import Highlight from "@tiptap/extension-highlight";
import TextAlign from "@tiptap/extension-text-align";
import { TextStyleKit } from "@tiptap/extension-text-style";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  AlignCenter,
  AlignJustify,
  AlignLeft,
  AlignRight,
  ArrowLeft,
  Baseline,
  Bold,
  Cloud,
  CloudOff,
  Highlighter,
  History,
  IndentIncrease,
  Italic,
  Link2,
  List,
  ListOrdered,
  Outdent,
  Printer,
  Redo2,
  RemoveFormatting,
  SeparatorHorizontal,
  Share2,
  Strikethrough,
  Underline,
  Undo2,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import {
  useEffect,
  useState,
  type FormEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { CollabMark } from "@/components/collab-mark";
import { AiCompletionPreview } from "@/components/ai-completion-preview";
import { useAiCompletion } from "@/components/use-ai-completion";
import type { PresenceConnection } from "@/components/collaborative-document";
import type { DocumentRecord } from "@/lib/gateway";
import type * as Y from "yjs";
import type { Awareness } from "y-protocols/awareness";

type DocumentEditorProps = {
  document: DocumentRecord;
  sharedDocument: Y.Doc;
  awareness: Awareness;
  status: "connecting" | "connected" | "disconnected" | "denied";
  presenceStatus: "loading" | "available" | "unavailable";
  connections: PresenceConnection[];
  currentUserId: string;
  pendingCount: number;
  isOwner: boolean;
  onRename: (title: string) => void;
  onSetSharing: (enabled: boolean) => Promise<void>;
};

type ToolbarButtonProps = {
  label: string;
  icon: LucideIcon;
  onClick: () => void;
  active?: boolean;
  disabled?: boolean;
  shortcut?: string;
};

type EditorMenuProps = {
  label: string;
  children: ReactNode;
};

type MenuItemProps = {
  label: string;
  onSelect?: () => void;
  hint?: string;
  disabled?: boolean;
};

const zoomLevels = [75, 90, 100, 125];
const fontSizes = [8, 9, 10, 11, 12, 14, 18, 24, 36];

function ToolbarButton({
  label,
  icon: Icon,
  onClick,
  active,
  disabled = false,
  shortcut,
}: ToolbarButtonProps) {
  const title = shortcut ? `${label} (${shortcut})` : label;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      aria-pressed={active}
      title={title}
      className={`grid size-8 shrink-0 place-items-center rounded-md outline-none transition focus-visible:ring-2 focus-visible:ring-[#1a73e8] ${
        active
          ? "bg-[#d3e3fd] text-[#0b57d0]"
          : "text-[#3c4043] hover:bg-[#e8eaed]"
      } disabled:cursor-not-allowed disabled:text-[#bdc1c6] disabled:hover:bg-transparent`}
    >
      <Icon aria-hidden="true" size={18} strokeWidth={2} />
    </button>
  );
}

function EditorMenu({ label, children }: EditorMenuProps) {
  return (
    <details className="editor-menu relative">
      <summary className="rounded px-2 py-1 text-sm text-[#3c4043] outline-none hover:bg-[#e8eaed] focus-visible:ring-2 focus-visible:ring-[#1a73e8]">
        {label}
      </summary>
      <div className="absolute top-full left-0 z-50 mt-1 min-w-56 rounded-lg border border-[#dadce0] bg-white py-1.5 shadow-xl">
        {children}
      </div>
    </details>
  );
}

function MenuItem({ label, onSelect, hint, disabled = false }: MenuItemProps) {
  function handleClick(event: ReactMouseEvent<HTMLButtonElement>) {
    onSelect?.();
    event.currentTarget.closest("details")?.removeAttribute("open");
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={disabled}
      className="flex w-full items-center justify-between gap-5 px-4 py-2 text-left text-sm text-[#3c4043] outline-none hover:bg-[#f1f3f4] focus-visible:bg-[#e8f0fe] disabled:cursor-not-allowed disabled:text-[#9aa0a6] disabled:hover:bg-transparent"
    >
      <span>{label}</span>
      {hint && <span className="text-xs whitespace-nowrap text-[#80868b]">{hint}</span>}
    </button>
  );
}

/** Renders the shared rich-text editor and the owner's access controls. */
export function DocumentEditor({
  document,
  sharedDocument,
  awareness,
  status,
  presenceStatus,
  connections,
  currentUserId,
  pendingCount,
  isOwner,
  onRename,
  onSetSharing,
}: DocumentEditorProps) {
  const router = useRouter();
  const [editingTitle, setEditingTitle] = useState<string | null>(null);
  const [showShare, setShowShare] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [shareError, setShareError] = useState("");
  const [copied, setCopied] = useState(false);
  const [zoom, setZoom] = useState(100);
  const [fontFamily, setFontFamily] = useState("Arial");
  const [fontSize, setFontSize] = useState("11");
  const [textColor, setTextColor] = useState("#202124");
  const [highlightColor, setHighlightColor] = useState("#fff2a8");
  const [showLinkInput, setShowLinkInput] = useState(false);
  const [linkValue, setLinkValue] = useState("");
  const [linkError, setLinkError] = useState("");

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        undoRedo: false,
        link: {
          openOnClick: false,
          defaultProtocol: "https",
          HTMLAttributes: {
            rel: "noopener noreferrer",
            target: "_blank",
          },
        },
      }),
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      TextStyleKit,
      Highlight.configure({ multicolor: true }),
      Collaboration.configure({ document: sharedDocument }),
      CollaborationCaret.configure({
        provider: { awareness },
        user: { name: "You", color: "#0b57d0" },
      }),
      AiCompletionPreview,
    ],
    immediatelyRender: false,
    editable: status === "connected",
    editorProps: {
      attributes: {
        "aria-label": "Document content",
        spellcheck: "true",
      },
    },
  }, [sharedDocument, awareness]);

  const completion = useAiCompletion(editor, document.id, status);

  const remoteUsers = [
    ...new Map(
      connections
        .filter((connection) => connection.userId !== currentUserId)
        .map((connection) => [connection.userId, connection]),
    ).values(),
  ];

  useEffect(() => {
    editor?.setEditable(status === "connected");
  }, [editor, status]);

  const formatting = useEditorState({
    editor,
    selector: ({ editor: currentEditor }) => {
      if (!currentEditor) {
        return {
          bold: false,
          italic: false,
          underline: false,
          strike: false,
          link: false,
          bulletList: false,
          orderedList: false,
          alignLeft: true,
          alignCenter: false,
          alignRight: false,
          alignJustify: false,
          blockType: "paragraph",
          canUndo: false,
          canRedo: false,
          canIndent: false,
          canOutdent: false,
          wordCount: 0,
          characterCount: 0,
        };
      }

      const text = currentEditor.getText();
      const trimmedText = text.trim();
      const blockType = currentEditor.isActive("heading", { level: 1 })
        ? "heading-1"
        : currentEditor.isActive("heading", { level: 2 })
          ? "heading-2"
          : currentEditor.isActive("heading", { level: 3 })
            ? "heading-3"
            : "paragraph";

      return {
        bold: currentEditor.isActive("bold"),
        italic: currentEditor.isActive("italic"),
        underline: currentEditor.isActive("underline"),
        strike: currentEditor.isActive("strike"),
        link: currentEditor.isActive("link"),
        bulletList: currentEditor.isActive("bulletList"),
        orderedList: currentEditor.isActive("orderedList"),
        alignLeft:
          currentEditor.isActive({ textAlign: "left" }) ||
          !currentEditor.isActive({ textAlign: "center" }) &&
            !currentEditor.isActive({ textAlign: "right" }) &&
            !currentEditor.isActive({ textAlign: "justify" }),
        alignCenter: currentEditor.isActive({ textAlign: "center" }),
        alignRight: currentEditor.isActive({ textAlign: "right" }),
        alignJustify: currentEditor.isActive({ textAlign: "justify" }),
        blockType,
        canUndo: currentEditor.can().undo(),
        canRedo: currentEditor.can().redo(),
        canIndent: currentEditor.can().sinkListItem("listItem"),
        canOutdent: currentEditor.can().liftListItem("listItem"),
        wordCount: trimmedText ? trimmedText.split(/\s+/u).length : 0,
        characterCount: text.length,
      };
    },
  });

  useEffect(() => {
    if (pendingCount === 0) {
      return;
    }

    function warnAboutUnsavedDraft(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }

    window.addEventListener("beforeunload", warnAboutUnsavedDraft);
    return () => window.removeEventListener("beforeunload", warnAboutUnsavedDraft);
  }, [pendingCount]);

  function returnToDocuments() {
    if (
      pendingCount === 0 ||
      window.confirm("Some changes have not been saved. Leave the editor anyway?")
    ) {
      router.push("/documents");
    }
  }

  function updateBlockType(value: string) {
    if (!editor) {
      return;
    }

    if (value === "heading-1") {
      editor.chain().focus().toggleHeading({ level: 1 }).run();
    } else if (value === "heading-2") {
      editor.chain().focus().toggleHeading({ level: 2 }).run();
    } else if (value === "heading-3") {
      editor.chain().focus().toggleHeading({ level: 3 }).run();
    } else {
      editor.chain().focus().setParagraph().run();
    }
  }

  function updateZoom(value: string) {
    const nextZoom = Number(value);

    if (zoomLevels.includes(nextZoom)) {
      setZoom(nextZoom);
    }
  }

  function openLinkInput() {
    setLinkError("");
    setShowLinkInput(true);
  }

  function applyLink(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = linkValue.trim();

    if (!value || !editor?.isEditable) {
      setLinkError("Enter a web address.");
      return;
    }

    const candidate = /^https?:\/\//iu.test(value) ? value : `https://${value}`;

    try {
      const url = new URL(candidate);

      if (url.protocol !== "http:" && url.protocol !== "https:") {
        setLinkError("Only HTTP and HTTPS links are supported.");
        return;
      }

      editor
        .chain()
        .focus()
        .extendMarkRange("link")
        .setLink({ href: url.toString() })
        .run();
      setLinkValue("");
      setLinkError("");
      setShowLinkInput(false);
    } catch {
      setLinkError("Enter a valid web address.");
    }
  }

  function removeLink() {
    editor?.chain().focus().extendMarkRange("link").unsetLink().run();
    setLinkValue("");
    setLinkError("");
    setShowLinkInput(false);
  }

  function clearFormatting() {
    editor?.chain().focus().unsetAllMarks().clearNodes().run();
  }

  async function changeSharing(enabled: boolean) {
    if (sharing) return;
    setSharing(true);
    setShareError("");
    try {
      await onSetSharing(enabled);
    } catch (cause) {
      setShareError(cause instanceof Error ? cause.message : "Could not change sharing.");
    } finally {
      setSharing(false);
    }
  }

  async function copyShareLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setShareError("");
    } catch {
      setShareError("Could not copy the link. Copy it from your browser address bar.");
    }
  }

  return (
    <main className="flex h-screen min-h-[640px] flex-col overflow-hidden bg-[#f8fafd] text-[#202124]">
      <header className="editor-chrome z-30 shrink-0 border-b border-[#e0e3e7] bg-white px-2 pt-2 sm:px-3">
        <div className="flex min-h-12 items-center gap-2">
          <button
            type="button"
            onClick={returnToDocuments}
            aria-label="Back to documents"
            title="Back to documents"
            className="grid size-10 shrink-0 place-items-center rounded-full text-[#3c4043] outline-none hover:bg-[#f1f3f4] focus-visible:ring-2 focus-visible:ring-[#1a73e8] sm:hidden"
          >
            <ArrowLeft aria-hidden="true" size={20} />
          </button>
          <button
            type="button"
            onClick={returnToDocuments}
            aria-label="Back to documents"
            title="Back to documents"
            className="hidden shrink-0 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-[#1a73e8] sm:block"
          >
            <CollabMark compact />
          </button>

          <div className="min-w-0 flex-1">
            <input
              value={editingTitle ?? document.title}
              maxLength={200}
              aria-label="Document title"
              disabled={status !== "connected"}
              onChange={(event) => setEditingTitle(event.target.value)}
              onBlur={() => {
                if (editingTitle === null) return;
                const nextTitle = editingTitle.trim() || "Untitled document";
                setEditingTitle(null);
                if (nextTitle !== document.title) onRename(nextTitle);
              }}
              className="block h-7 w-full max-w-[520px] truncate rounded border border-transparent px-1 text-[17px] leading-7 outline-none hover:border-[#dadce0] focus:border-[#1a73e8] sm:text-[18px]"
            />
            <div className="hidden items-center gap-1 sm:flex">
              <EditorMenu label="File">
                <MenuItem label="Print" hint="Ctrl+P" onSelect={() => window.print()} />
              </EditorMenu>
              <EditorMenu label="Edit">
                <MenuItem
                  label="Undo"
                  hint="Ctrl+Z"
                  disabled={status !== "connected" || !formatting?.canUndo}
                  onSelect={() => editor?.chain().focus().undo().run()}
                />
                <MenuItem
                  label="Redo"
                  hint="Ctrl+Y"
                  disabled={status !== "connected" || !formatting?.canRedo}
                  onSelect={() => editor?.chain().focus().redo().run()}
                />
                <MenuItem
                  label="Select all"
                  hint="Ctrl+A"
                  disabled={status !== "connected"}
                  onSelect={() => editor?.chain().focus().selectAll().run()}
                />
              </EditorMenu>
              <EditorMenu label="View">
                {zoomLevels.map((level) => (
                  <MenuItem
                    key={level}
                    label={`${level}%`}
                    hint={level === zoom ? "Current" : undefined}
                    onSelect={() => setZoom(level)}
                  />
                ))}
              </EditorMenu>
              <EditorMenu label="Insert">
                <MenuItem label="Link" hint="Ctrl+K" onSelect={openLinkInput} disabled={status !== "connected"} />
                <MenuItem
                  label="Horizontal line"
                  disabled={status !== "connected"}
                  onSelect={() => editor?.chain().focus().setHorizontalRule().run()}
                />
              </EditorMenu>
              <EditorMenu label="Format">
                <MenuItem label="Clear formatting" onSelect={clearFormatting} disabled={status !== "connected"} />
              </EditorMenu>
              <EditorMenu label="Tools">
                <MenuItem label="Grammar correction" hint="Backend required" disabled />
                <MenuItem label="Summarize document" hint="Backend required" disabled />
                <MenuItem label="Enhance writing" hint="Backend required" disabled />
              </EditorMenu>
              <EditorMenu label="Help">
                <div className="px-4 py-2 text-sm leading-6 text-[#5f6368]">
                  Use the toolbar or standard keyboard shortcuts to format your draft.
                </div>
              </EditorMenu>
            </div>
          </div>

          <div className="hidden items-center gap-2 xl:flex">
            <span className="flex items-center gap-1.5 rounded-full bg-[#f1f3f4] px-3 py-2 text-xs text-[#5f6368]">
              {status === "connected" ? <Cloud aria-hidden="true" size={14} /> : <CloudOff aria-hidden="true" size={14} />}
              {status === "connected" ? "Connected" : "Reconnecting"}
            </span>
          </div>
          {presenceStatus === "unavailable" ? (
            <span role="status" className="hidden text-xs text-[#735c0f] sm:inline">Presence unavailable</span>
          ) : presenceStatus === "available" ? (
            <div className="flex shrink-0 items-center -space-x-2" aria-label={`${remoteUsers.length} other editors online`}>
              {remoteUsers.slice(0, 4).map((member) => (
                <span
                  key={member.userId}
                  title={member.name}
                  aria-label={`${member.name} is editing`}
                  className="relative grid size-8 place-items-center overflow-hidden rounded-full border-2 border-white text-xs font-semibold text-white"
                  style={{ backgroundColor: member.color }}
                >
                  {member.imageUrl ? (
                    <Image src={member.imageUrl} alt="" width={32} height={32} unoptimized className="size-full object-cover" />
                  ) : member.name.slice(0, 1).toLocaleUpperCase()}
                </span>
              ))}
              {remoteUsers.length > 4 && (
                <span className="relative grid size-8 place-items-center rounded-full border-2 border-white bg-[#5f6368] text-xs text-white">
                  +{remoteUsers.length - 4}
                </span>
              )}
            </div>
          ) : null}
          <button
            type="button"
            disabled
            title="Version history requires the Raft-backed service"
            aria-label="Version history requires the backend"
            className="hidden size-9 place-items-center rounded-full text-[#9aa0a6] disabled:cursor-not-allowed md:grid"
          >
            <History aria-hidden="true" size={19} />
          </button>
          <button
            type="button"
            disabled={!isOwner}
            title={isOwner ? "Manage sharing" : "Only the document owner can change sharing"}
            onClick={() => setShowShare(true)}
            className="flex h-10 items-center gap-2 rounded-full bg-[#c2e7ff] px-3 text-sm font-medium text-[#001d35] hover:bg-[#a8d8f8] disabled:cursor-not-allowed disabled:opacity-50 sm:px-4"
          >
            <Share2 aria-hidden="true" size={17} />
            <span className="hidden sm:inline">Share</span>
          </button>
          <div className="mx-1 shrink-0">
            <UserButton />
          </div>
        </div>

        <fieldset disabled={status !== "connected"} className="my-2 flex min-h-10 items-center gap-1 overflow-x-auto rounded-full bg-[#edf2fa] px-2 py-1 [scrollbar-width:thin]">
          <ToolbarButton
            label="Undo"
            icon={Undo2}
            shortcut="Ctrl+Z"
            disabled={!formatting?.canUndo}
            onClick={() => editor?.chain().focus().undo().run()}
          />
          <ToolbarButton
            label="Redo"
            icon={Redo2}
            shortcut="Ctrl+Y"
            disabled={!formatting?.canRedo}
            onClick={() => editor?.chain().focus().redo().run()}
          />
          <ToolbarButton label="Print" icon={Printer} onClick={() => window.print()} />

          <span aria-hidden="true" className="mx-1 h-6 w-px shrink-0 bg-[#c7cacf]" />
          <label className="shrink-0">
            <span className="sr-only">Zoom</span>
            <select
              value={zoom}
              onChange={(event) => updateZoom(event.target.value)}
              className="h-8 w-[74px] rounded-md bg-transparent px-2 text-sm outline-none hover:bg-[#e1e7f0] focus-visible:ring-2 focus-visible:ring-[#1a73e8]"
            >
              {zoomLevels.map((level) => (
                <option key={level} value={level}>
                  {level}%
                </option>
              ))}
            </select>
          </label>

          <span aria-hidden="true" className="mx-1 h-6 w-px shrink-0 bg-[#c7cacf]" />
          <label className="shrink-0">
            <span className="sr-only">Paragraph style</span>
            <select
              value={formatting?.blockType ?? "paragraph"}
              disabled={!editor}
              onChange={(event) => updateBlockType(event.target.value)}
              className="h-8 w-[132px] rounded-md bg-transparent px-2 text-sm outline-none hover:bg-[#e1e7f0] focus-visible:ring-2 focus-visible:ring-[#1a73e8] disabled:text-[#9aa0a6]"
            >
              <option value="paragraph">Normal text</option>
              <option value="heading-1">Heading 1</option>
              <option value="heading-2">Heading 2</option>
              <option value="heading-3">Heading 3</option>
            </select>
          </label>
          <label className="shrink-0">
            <span className="sr-only">Font family</span>
            <select
              value={fontFamily}
              disabled={!editor}
              onChange={(event) => {
                const value = event.target.value;
                setFontFamily(value);
                editor?.chain().focus().setFontFamily(value).run();
              }}
              className="h-8 w-[118px] rounded-md bg-transparent px-2 text-sm outline-none hover:bg-[#e1e7f0] focus-visible:ring-2 focus-visible:ring-[#1a73e8] disabled:text-[#9aa0a6]"
            >
              <option value="Arial">Arial</option>
              <option value="Georgia">Georgia</option>
              <option value="Times New Roman">Times New Roman</option>
              <option value="Verdana">Verdana</option>
            </select>
          </label>
          <label className="shrink-0">
            <span className="sr-only">Font size</span>
            <select
              value={fontSize}
              disabled={!editor}
              onChange={(event) => {
                const value = event.target.value;
                setFontSize(value);
                editor?.chain().focus().setFontSize(`${value}pt`).run();
              }}
              className="h-8 w-16 rounded-md bg-transparent px-2 text-sm outline-none hover:bg-[#e1e7f0] focus-visible:ring-2 focus-visible:ring-[#1a73e8] disabled:text-[#9aa0a6]"
            >
              {fontSizes.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>

          <span aria-hidden="true" className="mx-1 h-6 w-px shrink-0 bg-[#c7cacf]" />
          <ToolbarButton
            label="Bold"
            icon={Bold}
            shortcut="Ctrl+B"
            active={formatting?.bold}
            disabled={!editor}
            onClick={() => editor?.chain().focus().toggleBold().run()}
          />
          <ToolbarButton
            label="Italic"
            icon={Italic}
            shortcut="Ctrl+I"
            active={formatting?.italic}
            disabled={!editor}
            onClick={() => editor?.chain().focus().toggleItalic().run()}
          />
          <ToolbarButton
            label="Underline"
            icon={Underline}
            shortcut="Ctrl+U"
            active={formatting?.underline}
            disabled={!editor}
            onClick={() => editor?.chain().focus().toggleUnderline().run()}
          />
          <ToolbarButton
            label="Strikethrough"
            icon={Strikethrough}
            active={formatting?.strike}
            disabled={!editor}
            onClick={() => editor?.chain().focus().toggleStrike().run()}
          />

          <label
            title="Text colour"
            className="relative grid size-8 shrink-0 place-items-center rounded-md text-[#3c4043] hover:bg-[#e1e7f0] focus-within:ring-2 focus-within:ring-[#1a73e8]"
          >
            <span className="sr-only">Text colour</span>
            <Baseline aria-hidden="true" size={18} />
            <span
              aria-hidden="true"
              className="absolute bottom-1 h-0.5 w-4 rounded"
              style={{ backgroundColor: textColor }}
            />
            <input
              type="color"
              value={textColor}
              disabled={!editor}
              onChange={(event) => {
                setTextColor(event.target.value);
                editor?.chain().focus().setColor(event.target.value).run();
              }}
              className="absolute inset-0 size-full opacity-0 disabled:cursor-not-allowed"
            />
          </label>
          <label
            title="Highlight colour"
            className="relative grid size-8 shrink-0 place-items-center rounded-md text-[#3c4043] hover:bg-[#e1e7f0] focus-within:ring-2 focus-within:ring-[#1a73e8]"
          >
            <span className="sr-only">Highlight colour</span>
            <Highlighter aria-hidden="true" size={18} />
            <span
              aria-hidden="true"
              className="absolute bottom-1 h-0.5 w-4 rounded"
              style={{ backgroundColor: highlightColor }}
            />
            <input
              type="color"
              value={highlightColor}
              disabled={!editor}
              onChange={(event) => {
                setHighlightColor(event.target.value);
                editor
                  ?.chain()
                  .focus()
                  .toggleHighlight({ color: event.target.value })
                  .run();
              }}
              className="absolute inset-0 size-full opacity-0 disabled:cursor-not-allowed"
            />
          </label>
          <ToolbarButton
            label="Insert link"
            icon={Link2}
            shortcut="Ctrl+K"
            active={formatting?.link}
            disabled={!editor}
            onClick={openLinkInput}
          />

          <span aria-hidden="true" className="mx-1 h-6 w-px shrink-0 bg-[#c7cacf]" />
          <ToolbarButton
            label="Align left"
            icon={AlignLeft}
            active={formatting?.alignLeft}
            disabled={!editor}
            onClick={() => editor?.chain().focus().setTextAlign("left").run()}
          />
          <ToolbarButton
            label="Align centre"
            icon={AlignCenter}
            active={formatting?.alignCenter}
            disabled={!editor}
            onClick={() => editor?.chain().focus().setTextAlign("center").run()}
          />
          <ToolbarButton
            label="Align right"
            icon={AlignRight}
            active={formatting?.alignRight}
            disabled={!editor}
            onClick={() => editor?.chain().focus().setTextAlign("right").run()}
          />
          <ToolbarButton
            label="Justify"
            icon={AlignJustify}
            active={formatting?.alignJustify}
            disabled={!editor}
            onClick={() => editor?.chain().focus().setTextAlign("justify").run()}
          />
          <ToolbarButton
            label="Bulleted list"
            icon={List}
            active={formatting?.bulletList}
            disabled={!editor}
            onClick={() => editor?.chain().focus().toggleBulletList().run()}
          />
          <ToolbarButton
            label="Numbered list"
            icon={ListOrdered}
            active={formatting?.orderedList}
            disabled={!editor}
            onClick={() => editor?.chain().focus().toggleOrderedList().run()}
          />
          <ToolbarButton
            label="Decrease indent"
            icon={Outdent}
            disabled={!formatting?.canOutdent}
            onClick={() => editor?.chain().focus().liftListItem("listItem").run()}
          />
          <ToolbarButton
            label="Increase indent"
            icon={IndentIncrease}
            disabled={!formatting?.canIndent}
            onClick={() => editor?.chain().focus().sinkListItem("listItem").run()}
          />
          <ToolbarButton
            label="Insert horizontal line"
            icon={SeparatorHorizontal}
            disabled={!editor}
            onClick={() => editor?.chain().focus().setHorizontalRule().run()}
          />
          <ToolbarButton
            label="Clear formatting"
            icon={RemoveFormatting}
            disabled={!editor}
            onClick={clearFormatting}
          />
        </fieldset>
      </header>

      {showShare && isOwner && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-[#202124]/40 px-4">
          <div role="dialog" aria-modal="true" aria-labelledby="share-heading" className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <div className="flex items-center justify-between">
              <h2 id="share-heading" className="text-lg font-medium">Share this document</h2>
              <button type="button" onClick={() => setShowShare(false)} aria-label="Close sharing" className="rounded-full p-2 hover:bg-[#f1f3f4]"><X aria-hidden="true" size={18} /></button>
            </div>
            <p className="mt-3 text-sm text-[#5f6368]">
              {document.shareEnabled
                ? "Anyone signed in with this link can edit."
                : "Only you can open this document."}
            </p>
            <button type="button" disabled={sharing} onClick={() => void changeSharing(!document.shareEnabled)} className="mt-5 rounded-full border border-[#dadce0] px-4 py-2 text-sm font-medium text-[#1967d2] hover:bg-[#e8f0fe] disabled:opacity-50">
              {sharing ? "Updating…" : document.shareEnabled ? "Turn off link access" : "Allow signed-in editors with link"}
            </button>
            {document.shareEnabled && (
              <div className="mt-5 flex gap-2">
                <input aria-label="Document link" readOnly value={typeof window === "undefined" ? "" : window.location.href} className="min-w-0 flex-1 rounded-lg border border-[#dadce0] px-3 text-sm" />
                <button type="button" onClick={() => void copyShareLink()} className="rounded-full bg-[#1a73e8] px-4 py-2 text-sm font-medium text-white">{copied ? "Copied" : "Copy link"}</button>
              </div>
            )}
            {shareError && <p role="alert" className="mt-3 text-sm text-[#b3261e]">{shareError}</p>}
          </div>
        </div>
      )}

      {showLinkInput && (
        <div className="editor-chrome z-20 border-b border-[#d2e3fc] bg-[#e8f0fe] px-4 py-3">
          <form
            onSubmit={applyLink}
            className="mx-auto flex max-w-2xl flex-wrap items-start gap-2"
          >
            <label className="min-w-56 flex-1">
              <span className="sr-only">Link address</span>
              <input
                type="text"
                value={linkValue}
                onChange={(event) => {
                  setLinkValue(event.target.value);
                  setLinkError("");
                }}
                placeholder="example.com"
                autoFocus
                className="h-10 w-full rounded-md border border-[#a8c7fa] bg-white px-3 text-sm outline-none focus:border-[#1a73e8] focus:ring-1 focus:ring-[#1a73e8]"
              />
              {linkError && (
                <span role="alert" className="mt-1 block text-xs text-[#b3261e]">
                  {linkError}
                </span>
              )}
            </label>
            <button
              type="submit"
              className="h-10 rounded-full bg-[#1a73e8] px-4 text-sm font-medium text-white outline-none hover:bg-[#185abc] focus-visible:ring-2 focus-visible:ring-[#1a73e8] focus-visible:ring-offset-2"
            >
              Apply
            </button>
            <button
              type="button"
              onClick={removeLink}
              disabled={!formatting?.link}
              className="h-10 rounded-full px-4 text-sm font-medium text-[#1967d2] outline-none hover:bg-white/60 focus-visible:ring-2 focus-visible:ring-[#1a73e8] disabled:cursor-not-allowed disabled:text-[#9aa0a6]"
            >
              Remove
            </button>
            <button
              type="button"
              onClick={() => {
                setShowLinkInput(false);
                setLinkError("");
              }}
              aria-label="Close link editor"
              className="grid size-10 place-items-center rounded-full text-[#3c4043] outline-none hover:bg-white/60 focus-visible:ring-2 focus-visible:ring-[#1a73e8]"
            >
              <X aria-hidden="true" size={18} />
            </button>
          </form>
        </div>
      )}

      <div className="document-ruler editor-chrome relative z-10 h-6 shrink-0 border-b border-[#d5d9dd] bg-white shadow-sm">
        <div
          className="mx-auto h-full max-w-[816px] border-x border-[#e1e4e8]"
          style={{
            backgroundImage:
              "repeating-linear-gradient(90deg, #9aa0a6 0, #9aa0a6 1px, transparent 1px, transparent 24px)",
            backgroundPosition: "0 18px",
            backgroundRepeat: "repeat-x",
            backgroundSize: "24px 6px",
          }}
        >
          <span className="sr-only">Document ruler</span>
        </div>
      </div>

      <section className="document-workspace flex flex-1 justify-center overflow-auto bg-[#f1f3f4] px-3 pt-5 pb-24 sm:px-6 sm:pt-8">
        <div
          className="document-page shrink-0 overflow-hidden bg-white shadow-[0_1px_4px_rgba(60,64,67,0.28)]"
          style={{ zoom: zoom / 100 }}
        >
          <EditorContent editor={editor} />
        </div>
      </section>

      {(completion.suggestion || completion.loading || completion.error) && (
        <div role="group" className="fixed right-4 bottom-16 z-30 max-w-[min(480px,calc(100vw-32px))] rounded-xl border border-[#dadce0] bg-white p-3 text-sm shadow-lg" aria-label="AI completion">
          {completion.suggestion && (
            <div className="flex flex-wrap gap-2">
              <p className="sr-only" aria-live="polite">Suggested completion: {completion.suggestion.text}</p>
              <button type="button" onClick={() => completion.accept("all")} className="rounded-full bg-[#1a73e8] px-3 py-1.5 text-white">Accept all <span className="text-xs opacity-80">Tab</span></button>
              <button type="button" onClick={() => completion.accept("word")} className="rounded-full border border-[#dadce0] px-3 py-1.5">Accept word</button>
              <button type="button" onClick={() => completion.accept("line")} className="rounded-full border border-[#dadce0] px-3 py-1.5">Accept line</button>
              <button type="button" disabled={completion.loading} onClick={completion.tryAnother} className="rounded-full border border-[#dadce0] px-3 py-1.5 disabled:opacity-50">Try another</button>
              <button type="button" onClick={completion.dismiss} className="rounded-full border border-[#dadce0] px-3 py-1.5">Dismiss</button>
            </div>
          )}
          {completion.loading && <p role="status" className="mt-2 text-[#5f6368]">Writing suggestion…</p>}
          {completion.error && <div className="mt-2 flex items-center gap-2"><p role="alert" className="text-[#b3261e]">{completion.error}</p><button type="button" onClick={completion.suggestion ? completion.tryAnother : completion.retry} className="text-[#1967d2]">Retry</button></div>}
        </div>
      )}

      <footer className="document-footer pointer-events-none fixed right-4 bottom-4 left-4 z-20 flex items-end justify-between gap-4">
        <span className="rounded-full border border-[#dadce0] bg-white/95 px-3 py-1.5 text-xs text-[#5f6368] shadow-sm backdrop-blur">
          {formatting?.wordCount ?? 0} words · {formatting?.characterCount ?? 0} characters
        </span>
        <span role="status" className={`flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium shadow-sm backdrop-blur ${status === "connected" && pendingCount === 0 ? "border-[#b7dfc4] bg-[#e6f4ea]/95 text-[#137333]" : "border-[#f0d58c] bg-[#fef7e0]/95 text-[#735c0f]"}`}>
          {status === "connected" ? <Cloud aria-hidden="true" size={13} /> : <CloudOff aria-hidden="true" size={13} />}
          {status === "connected"
            ? pendingCount > 0 ? "Saving…" : "Saved"
            : pendingCount > 0 ? "Reconnecting — changes not saved" : "Reconnecting…"}
        </span>
      </footer>
    </main>
  );
}
