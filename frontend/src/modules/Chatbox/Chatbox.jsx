import { useState } from "react";
import Avatar from "../Avatar/Avatar";
import "./Chatbox.css";

export default function Chatbox({
  messages,
  draft,
  setDraft,
  onSend,
  disabled = false,
  thinkingSide = null,
  thinkingStatus = "thinking",
  subscriptError = null,
  myColor = null,
}) {
  const thinkingMine = thinkingSide && thinkingSide === myColor;
  const thinkingOpp = thinkingSide && thinkingSide !== myColor;
  const showErrorSub = !!subscriptError && !thinkingMine;
  const thinkingLabel =
    thinkingStatus === "retrying" ? "wrong move, retrying" : "thinking";
  const [openIdx, setOpenIdx] = useState(null);
  return (
    <aside className="chat-pane">
      <div className="chat-log">
        {messages.map((m, i) => {
          if (m.from === "system") {
            const sideClass =
              m.side === myColor
                ? "msg-mine"
                : m.side
                  ? "msg-other"
                  : "";
            const isOpen = openIdx === i;
            return (
              <div key={i} className={`msg msg-system ${sideClass}`}>
                <div className="msg-row">
                  <span className="msg-text">{m.text}</span>
                  {m.explanation && (
                    <button
                      type="button"
                      className="explain-btn"
                      onClick={() => setOpenIdx(isOpen ? null : i)}
                      aria-expanded={isOpen}
                      aria-label="Why this move?"
                    >
                      ?
                    </button>
                  )}
                </div>
                {m.explanation && isOpen && (
                  <div className="explain-card">
                    <div className="explain-row">
                      <strong>Prior tone:</strong>{" "}
                      {m.explanation.priorTone || "—"}
                    </div>
                    <div className="explain-row">
                      <strong>Message intent:</strong>{" "}
                      {m.explanation.intent || "—"}
                    </div>
                    <div className="explain-row">
                      <strong>Why this move:</strong>{" "}
                      {m.explanation.rationale || "—"}
                    </div>
                  </div>
                )}
              </div>
            );
          }
          const isMine = m.from === myColor;
          return (
            <div
              key={i}
              className={`msg msg-${m.from} ${isMine ? "msg-mine" : "msg-other"}`}
            >
              {!isMine && <Avatar side={m.from} />}
              <span className="msg-text">{m.text}</span>
              {isMine && <Avatar side={m.from} />}
            </div>
          );
        })}
        {thinkingMine && (
          <div className="chat-thinking-sub msg-mine" aria-live="polite">
            <span className="thinking-text">{thinkingLabel}</span>
            <span className="thinking-dot" />
            <span className="thinking-dot" />
            <span className="thinking-dot" />
          </div>
        )}
        {showErrorSub && (
          <div
            className="chat-thinking-sub chat-thinking-sub-error msg-mine"
            role="alert"
          >
            <span className="thinking-text">{subscriptError}</span>
          </div>
        )}
        {thinkingOpp && (
          <div
            className="chat-typing msg-other"
            data-side={thinkingSide}
            aria-live="polite"
          >
            <span className="thinking-dot" />
            <span className="thinking-dot" />
            <span className="thinking-dot" />
          </div>
        )}
      </div>
      <form className="chat-input" onSubmit={onSend}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={disabled ? "Waiting for opponent..." : "Type a phrase..."}
          disabled={disabled}
          autoFocus
        />
        <button type="submit" disabled={disabled || !draft.trim()}>Send</button>
      </form>
    </aside>
  );
}
