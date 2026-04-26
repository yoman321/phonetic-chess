import Avatar from "../Avatar/Avatar";
import "./Chatbox.css";

export default function Chatbox({ messages, draft, setDraft, onSend }) {
  return (
    <aside className="chat-pane">
      <div className="chat-log">
        {messages.map((m, i) => {
          if (m.from === "system") {
            return (
              <div key={i} className="msg msg-system">
                <span className="msg-text">{m.text}</span>
              </div>
            );
          }
          return (
            <div key={i} className={`msg msg-${m.from}`}>
              {m.from === "black" && <Avatar side="black" />}
              <span className="msg-text">{m.text}</span>
              {m.from === "white" && <Avatar side="white" />}
            </div>
          );
        })}
      </div>
      <form className="chat-input" onSubmit={onSend}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Type a phrase..."
          autoFocus
        />
        <button type="submit">Send</button>
      </form>
    </aside>
  );
}
