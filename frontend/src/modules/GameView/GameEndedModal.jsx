import { useNavigate } from "react-router-dom";
import "./GameEndedModal.css";

export default function GameEndedModal() {
  const navigate = useNavigate();

  return (
    <div className="ended-backdrop">
      <div className="ended-modal">
        <h2 className="ended-title">Oops — game ended</h2>
        <p className="ended-copy">
          Nobody was here for a while, so this game is closed. The moves are kept
          — start a new game, or head back to the menu.
        </p>
        <div className="ended-actions">
          <button className="menu-btn" onClick={() => navigate("/")}>
            Back to menu
          </button>
          <button
            className="menu-btn primary"
            onClick={() => navigate("/", { state: { openModal: "create" } })}
          >
            New game
          </button>
        </div>
      </div>
    </div>
  );
}
