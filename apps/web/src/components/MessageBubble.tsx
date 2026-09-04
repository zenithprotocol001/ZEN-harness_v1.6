import React from "react";
import { Message, ToolCall } from "../types/chat";
import { Markdown, ToolResult } from "./Markdown";
import { BranchButton } from "./BranchButton";
import { Icon } from "./icons";

/**
 * <MessageBubble /> (v1.5.0.1 redesign): a single chat message.
 *
 * Layout:
 *   [ avatar ]  [ bubble content
 *                 (Markdown for text, ToolResult/ToolCallList for tool
 *                  messages, OutOfLineAttachmentPreview for binary
 *                  attachments, <pre> for inline text attachments) ]
 *
 * Max-width on the bubble is 720px (the ChatGPT/Claude convention);
 * the rest of the row is empty whitespace. The role is conveyed by
 * the avatar color, not by a header label.
 *
 * Developer-mode (v1.5.0) affordances:
 *   - The Fork button is shown only when `developerMode` is true.
 *     In the default view, the button is hidden; the v1.5.0 server
 *     feature still works, just no UI affordance.
 *   - This is the v1.5.0.1 default; a v1.5.1 can add a "branch tree"
 *     popover in the same developer-mode group.
 */
export function MessageBubble(props: {
  message: Message;
  activeId: string;
  onForked: (newId: string) => void;
  developerMode?: boolean;
}) {
  const m = props.message;
  const isUser = m.role === "user";
  const isTool = m.role === "tool";
  const isAssistant = m.role === "assistant";
  const avatarChar = isUser ? "U" : isTool ? "T" : "A";
  const avatarClass = isUser
    ? "chat-bubble-avatar-user"
    : isTool
      ? "chat-bubble-avatar-tool"
      : "chat-bubble-avatar-assistant";
  return (
    <div
      className={`chat-bubble ${isUser ? "chat-bubble-user" : isAssistant ? "chat-bubble-assistant" : "chat-bubble-tool"}`}
      data-role={m.role}
    >
      <div className={avatarClass} title={m.role}>
        {avatarChar}
      </div>
      <div className="chat-bubble-body">
        <div className="chat-bubble-content">
          {m.role === "tool" && m.tool_calls ? (
            <ToolCallList calls={m.tool_calls} />
          ) : m.role === "tool" ? (
            <ToolResult text={m.content} />
          ) : (
            <Markdown source={m.content} />
          )}
        </div>
        {m.attachments && m.attachments.length > 0 && (
          <div className="message-attachments">
            {m.attachments.map((a) => (
              <OutOfLineAttachmentPreview key={a.ref} ref_={a} />
            ))}
          </div>
        )}
        {m.inline_attachments && m.inline_attachments.length > 0 && (
          <div className="message-inline-attachments">
            {m.inline_attachments.map((a, i) => (
              <pre key={i} className="inline-attachment" data-mime={a.mime}>
                {a.body}
              </pre>
            ))}
          </div>
        )}
        <div className="chat-bubble-meta">
          {m.tokens && (
            <span className="chat-bubble-tokens">
              tokens {m.tokens.prompt + m.tokens.completion}
            </span>
          )}
          {m.truncated && <span className="chat-bubble-truncated">truncated</span>}
          {m.cancelled && (
            <span
              className="chat-bubble-cancelled"
              data-testid="chat-bubble-cancelled"
              aria-label="Generation was cancelled"
            >
              <Icon name="cancelled" size={10} />
              <span>cancelled</span>
            </span>
          )}
          {props.developerMode && (
            <span className="chat-bubble-developer">
              <BranchButton messageId={m.id} sessionId={props.activeId} onForked={props.onForked} />
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function ToolCallList(props: { calls: ToolCall[] }) {
  return (
    <div className="tool-calls">
      {props.calls.map((tc, i) => (
        <div key={i} className="tool-call">
          <strong>
            <Icon name="fork" size={11} /> {tc.function?.name || "tool"}
          </strong>
          {tc.function?.arguments && (
            <pre className="tool-call-args">{tc.function.arguments}</pre>
          )}
        </div>
      ))}
    </div>
  );
}

function OutOfLineAttachmentPreview(props: {
  ref_: { ref: string; mime: string; sha256: string; size: number };
}) {
  const [src, setSrc] = React.useState<string | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token =
          (document.querySelector('meta[name="dhc-token"]') as HTMLMetaElement | null)
            ?.content ?? "";
        const r = await fetch(`/api/attachments/${encodeURIComponent(props.ref_.ref)}`, {
          headers: token ? { "X-DHC-Token": token } : {},
        });
        if (!r.ok) {
          setErr(`HTTP ${r.status}`);
          return;
        }
        const blob = await r.blob();
        if (cancelled) return;
        setSrc(URL.createObjectURL(blob));
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
      if (src) URL.revokeObjectURL(src);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.ref_.ref]);
  if (err) {
    return (
      <div className="attachment-preview attachment-preview-err" data-ref={props.ref_.ref}>
        {`ref ${props.ref_.ref} (load failed: ${err})`}
      </div>
    );
  }
  if (!src) {
    return (
      <div className="attachment-preview attachment-preview-loading" data-ref={props.ref_.ref}>
        {`ref ${props.ref_.ref} (loading…)`}
      </div>
    );
  }
  if (props.ref_.mime.startsWith("image/")) {
    return (
      <img
        className="attachment-preview attachment-preview-image"
        data-ref={props.ref_.ref}
        src={src}
        alt=""
      />
    );
  }
  if (props.ref_.mime.startsWith("audio/")) {
    return (
      <audio
        className="attachment-preview attachment-preview-audio"
        data-ref={props.ref_.ref}
        controls
        src={src}
      />
    );
  }
  if (props.ref_.mime === "application/pdf") {
    return (
      <embed
        className="attachment-preview attachment-preview-pdf"
        data-ref={props.ref_.ref}
        src={src}
        type="application/pdf"
      />
    );
  }
  return (
    <a
      className="attachment-preview attachment-preview-generic"
      data-ref={props.ref_.ref}
      href={src}
      download
    >
      {`${props.ref_.mime} (${props.ref_.size} bytes)`}
    </a>
  );
}
