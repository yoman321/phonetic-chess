import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { getSession, joinSession } from "../../api";
import { setStoredToken } from "../../storage";

function parseSessionId(input) {
  const trimmed = input.trim();
  if (!trimmed) return "";
  const match = trimmed.match(/sessionId\/([^/?#\s]+)/);
  if (match) return match[1];
  return trimmed;
}

export default function JoinGameModal({ onClose }) {
  const navigate = useNavigate();
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const handleJoin = async (e) => {
    e.preventDefault();
    const id = parseSessionId(input);
    if (!id) return;

    setBusy(true);
    setError(null);
    try {
      const session = await getSession(id);
      if (!session) {
        setError("Session not found.");
        return;
      }
      const join = await joinSession(id, null);
      setStoredToken(id, join.playerToken);
      navigate(`/sessionId/${id}`);
    } catch (err) {
      if (err.message === "session_full") {
        setError("This game is already full.");
      } else {
        setError(err.message);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 className="modal-title">Join Game</h2>
        <p className="modal-subtitle">
          Paste the session URL or session ID your friend shared.
        </p>

        <form onSubmit={handleJoin}>
          <input
            className="session-input"
            type="text"
            placeholder="https://… or session id"
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              setError(null);
            }}
            autoFocus
          />

          {error && <p className="modal-error">{error}</p>}

          <div className="modal-actions">
            <button type="button" className="menu-btn" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="menu-btn primary"
              disabled={!input.trim() || busy}
            >
              {busy ? "Joining…" : "Join"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
