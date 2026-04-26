import { useState } from "react";
import CreateGameModal from "./CreateGameModal";
import JoinGameModal from "./JoinGameModal";
import "./Menu.css";

export default function Menu() {
  const [openModal, setOpenModal] = useState(null);
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
