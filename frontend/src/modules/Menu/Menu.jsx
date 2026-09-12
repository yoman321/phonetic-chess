import { useState } from "react";
import { useLocation } from "react-router-dom";
import CreateGameModal from "./CreateGameModal";
import JoinGameModal from "./JoinGameModal";
import "./Menu.css";

export default function Menu() {
  // Seeded from router state so GameEndedModal's "New game" lands on the
  // colour picker rather than on a bare menu.
  const { state } = useLocation();
  const [openModal, setOpenModal] = useState(state?.openModal ?? null);
  const close = () => setOpenModal(null);

  return (
    <div className="menu">
      <div className="menu-card">
        <h1 className="menu-title">Phonetic Chess</h1>
        <p className="menu-subtitle">Speak your moves. Play with a friend.</p>

        <div className="menu-actions">
          <button className="menu-btn primary" onClick={() => setOpenModal("create")}>
            Create Game
          </button>
          <button className="menu-btn" onClick={() => setOpenModal("join")}>
            Join Game
          </button>
        </div>
      </div>

      {openModal === "create" && <CreateGameModal onClose={close} />}
      {openModal === "join" && <JoinGameModal onClose={close} />}
    </div>
  );
}
