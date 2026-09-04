import React from "react";

/** MIME allowlist mirrors the v1.5.0 server-side allowlist (ADR-0020). */
export const MIME_ALLOWLIST_INLINE = ["text/plain", "text/markdown", "image/svg+xml"] as const;
export const MIME_ALLOWLIST_OUT_OF_LINE = [
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
  "audio/mpeg",
  "audio/wav",
  "application/pdf",
] as const;
export const MIME_ALLOWLIST = [
  ...MIME_ALLOWLIST_INLINE,
  ...MIME_ALLOWLIST_OUT_OF_LINE,
] as const;
export type AllowedMime = (typeof MIME_ALLOWLIST)[number];

export function isMimeAllowed(mime: string): mime is AllowedMime {
  return (MIME_ALLOWLIST as readonly string[]).includes(mime);
}

export function isMimeInline(mime: string): boolean {
  return (MIME_ALLOWLIST_INLINE as readonly string[]).includes(mime);
}

export interface AttachmentsPanelProps {
  sessionId: string;
}

/**
 * <AttachmentsPanel /> (v1.5.0, ADR-0020) — file upload + preview +
 * delete.
 *
 * Routing: the MIME class decides. Text-like MIME
 * (`text/plain`, `text/markdown`, `image/svg+xml`) is rejected here
 * because text/SVG goes *inline* in the message body, not through
 * the binary attachment service. The panel only handles the
 * out-of-line MIME class (image / audio / pdf).
 *
 * Preview: out-of-line binary is rendered as a `data:` URI
 * (asset domain — the bytes are the bytes; no 62-filter).
 *
 * Errors: a disallowed MIME is rejected client-side with a clear
 * message; the server is the secondary check.
 */
export function AttachmentsPanel({ sessionId }: AttachmentsPanelProps) {
  const [ref, setRef] = React.useState<string | null>(null);
  const [mime, setMime] = React.useState<string | null>(null);
  const [size, setSize] = React.useState<number | null>(null);
  const [previewUrl, setPreviewUrl] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  const onFile = React.useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setErr(null);
      setBusy(true);
      try {
        if (!isMimeAllowed(file.type)) {
          setErr(
            `Disallowed MIME ${file.type || "(unknown)"}. Allowed: ${MIME_ALLOWLIST.join(", ")}`
          );
          return;
        }
        if (isMimeInline(file.type)) {
          setErr(
            `MIME ${file.type} goes inline in the message body, not through the attachment service.`
          );
          return;
        }
        const token = (document.querySelector('meta[name="dhc-token"]') as HTMLMetaElement | null)?.content ?? "";
        const res = await fetch(
          `/api/sessions/${encodeURIComponent(sessionId)}/attachments`,
          {
            method: "POST",
            headers: {
              "Content-Type": file.type,
              ...(token ? { "X-DHC-Token": token } : {}),
            },
            body: file,
          }
        );
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          setErr(body?.error ?? `HTTP ${res.status}`);
          return;
        }
        const data = await res.json();
        setRef(data.ref);
        setMime(data.mime);
        setSize(data.size);
        // Build the data: URI for preview.
        const dataUri = `data:${file.type};base64,${arrayBufferToBase64(await file.arrayBuffer())}`;
        setPreviewUrl(dataUri);
      } catch (e2) {
        setErr(e2 instanceof Error ? e2.message : String(e2));
      } finally {
        setBusy(false);
      }
    },
    [sessionId]
  );

  const onDelete = React.useCallback(async () => {
    if (!ref) return;
    setErr(null);
    setBusy(true);
    try {
      const token = (document.querySelector('meta[name="dhc-token"]') as HTMLMetaElement | null)?.content ?? "";
      const res = await fetch(`/api/attachments/${encodeURIComponent(ref)}`, {
        method: "DELETE",
        headers: token ? { "X-DHC-Token": token } : {},
      });
      if (!res.ok && res.status !== 404) {
        const body = await res.json().catch(() => ({}));
        setErr(body?.error ?? `HTTP ${res.status}`);
        return;
      }
      setRef(null);
      setMime(null);
      setSize(null);
      setPreviewUrl(null);
    } finally {
      setBusy(false);
    }
  }, [ref]);

  return (
    <div data-testid="attachments-panel">
      <input
        type="file"
        data-testid="attachments-file-input"
        onChange={onFile}
        disabled={busy}
      />
      {err ? <div role="alert" data-testid="attachments-error">{err}</div> : null}
      {ref && mime && previewUrl ? (
        <div data-testid="attachments-preview">
          <div>{`Ref: ${ref} (${mime}, ${size} bytes)`}</div>
          {renderPreview(mime, previewUrl)}
          <button
            type="button"
            data-testid="attachments-delete"
            onClick={onDelete}
            disabled={busy}
          >
            Delete
          </button>
        </div>
      ) : null}
    </div>
  );
}

function renderPreview(mime: string, dataUri: string): React.ReactNode {
  if (mime.startsWith("image/")) {
    return <img data-testid="attachments-preview-img" src={dataUri} alt="" />;
  }
  if (mime.startsWith("audio/")) {
    return <audio data-testid="attachments-preview-audio" controls src={dataUri} />;
  }
  if (mime === "application/pdf") {
    return <embed data-testid="attachments-preview-pdf" src={dataUri} type="application/pdf" />;
  }
  return null;
}

function arrayBufferToBase64(buf: ArrayBuffer): string {
  // Browser-safe base64 encoder.
  const bytes = new Uint8Array(buf);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  // eslint-disable-next-line no-undef
  return btoa(binary);
}
