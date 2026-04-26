import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { createSession } from "../../api";
import { setStoredToken } from "../../storage";

function shareUrlFor(sessionId) {
  return `${window.location.origin}/sessionId/${sessionId}`;
}

export default function CreateGameModal({ onClose }) {
  const navigate = useNavigate();
  const [session, setSession] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const pickColor = async (color) => {
    setBusy(true);
    setError(null);
    try {
      const s = await createSession(color);
      setStoredToken(s.id, s.playerToken);
      setSession(s);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const url = session ? shareUrlFor(session.id) : "";

  const handleCopy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {!session ? (
          <>
            <h2 className="modal-title">Create Game</h2>
            <p className="modal-subtitle">Which color do you want to play?</p>

            <div className="color-choices">
              <button
                className="color-choice"
                onClick={() => pickColor("white")}
                disabled={busy}
              >
                <span className="color-king white">♚</span>
                White
              </button>
              <button
                className="color-choice"
                onClick={() => pickColor("black")}
                disabled={busy}
              >
                <span className="color-king black">♚</span>
                Black
              </button>
              <button
                className="color-choice"
                onClick={() => pickColor("random")}
                disabled={busy}
              >
                <span className="color-king random">
                  <span className="king-w">♚</span>
                  <span className="king-b">♚</span>
                </span>
                Random
              </button>
            </div>

            {error && <p className="modal-error">Error: {error}</p>}

            <div className="modal-actions">
              <button className="menu-btn" onClick={onClose}>
                Cancel
              </button>
            </div>
          </>
        ) : (
          <>
            <h2 className="modal-title">Game Created</h2>
            <p className="modal-subtitle">
              You're playing <strong>{session.color}</strong>. Share this URL
              with your friend.
            </p>

            <div className="session-id-row">
              <code className="session-id">{url}</code>
              <button className="menu-btn small" onClick={handleCopy}>
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>

            <div className="modal-actions">
              <button className="menu-btn" onClick={onClose}>
                Cancel
              </button>
              <button
                className="menu-btn primary"
                onClick={() => navigate(`/sessionId/${session.id}`)}
              >
                Enter Game
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
