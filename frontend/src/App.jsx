import { useMemo, useState } from "react";
import { Chess } from "chess.js";
import ChessBoard from "./modules/Chessboard/ChessBoard";
import Chatbox from "./modules/Chatbox/Chatbox";
import "./App.css";

export default function App() {
  const [game] = useState(() => new Chess());
  const [position, setPosition] = useState(() => game.fen());
  const [messages, setMessages] = useState([
    { from: "system", text: "You are White. Make a move on the board." },
  ]);
  const [draft, setDraft] = useState("");
  const [selectedSquare, setSelectedSquare] = useState(null);
  const [legalTargets, setLegalTargets] = useState([]);

  const turnLabel = position && game.turn() === "w" ? "White" : "Black";

  const handleSend = (e) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;

    setMessages((m) => [...m, { from: "user", text }]);
    setDraft("");

    const moves = game.moves();
    if (moves.length === 0) {
      setMessages((m) => [...m, { from: "system", text: "Game over." }]);
      return;
    }
    const pick = moves[Math.floor(Math.random() * moves.length)];
    game.move(pick);
    setPosition(game.fen());
    setMessages((m) => [
      ...m,
      { from: "system", text: `Matched (stub) → ${pick}` },
    ]);
  };

  const tryMove = (from, to) => {
    try {
      const move = game.move({ from, to, promotion: "q" });
      if (!move) return null;
      setPosition(game.fen());
      const isWhite = move.color === "w";
      setMessages((m) => [
        ...m,
        { from: isWhite ? "white" : "black", text: move.san },
      ]);
      return move;
    } catch {
      return null;
    }
  };

  const handlePieceDrop = ({ sourceSquare, targetSquare }) => {
    if (!targetSquare) return false;
    setSelectedSquare(null);
    setLegalTargets([]);
    return tryMove(sourceSquare, targetSquare) !== null;
  };

  const showMovesFor = (square) => {
    const moves = game.moves({ square, verbose: true });
    if (moves.length === 0) {
      setSelectedSquare(null);
      setLegalTargets([]);
      return false;
    }
    setSelectedSquare(square);
    setLegalTargets(moves.map((m) => m.to));
    return true;
  };

  const handleSquareClick = ({ square }) => {
    if (selectedSquare && legalTargets.includes(square)) {
      tryMove(selectedSquare, square);
      setSelectedSquare(null);
      setLegalTargets([]);
      return;
    }
    if (selectedSquare === square) {
      setSelectedSquare(null);
      setLegalTargets([]);
      return;
    }
    showMovesFor(square);
  };

  const squareStyles = useMemo(() => {
    const styles = {};
    if (selectedSquare) {
      styles[selectedSquare] = { background: "rgba(100, 108, 255, 0.45)" };
    }
    for (const sq of legalTargets) {
      const isCapture = !!game.get(sq);
      styles[sq] = isCapture
        ? {
            background:
              "radial-gradient(circle, transparent 55%, rgba(100, 108, 255, 0.55) 56%)",
          }
        : {
            background:
              "radial-gradient(circle, rgba(100, 108, 255, 0.55) 22%, transparent 23%)",
          };
    }
    return styles;
  }, [selectedSquare, legalTargets, game]);

  const reset = () => {
    game.reset();
    setPosition(game.fen());
    setSelectedSquare(null);
    setLegalTargets([]);
    setMessages([{ from: "system", text: "New game. You are White — make a move." }]);
  };

  return (
    <div className="app">
      <header className="app-header">
        <h1>Phonetic Chess</h1>
        <button onClick={reset} className="reset-btn">New game</button>
      </header>

      <main className="layout">
        <ChessBoard
          position={position}
          turnLabel={turnLabel}
          squareStyles={squareStyles}
          onPieceDrop={handlePieceDrop}
          onSquareClick={handleSquareClick}
        />
        <Chatbox
          messages={messages}
          draft={draft}
          setDraft={setDraft}
          onSend={handleSend}
        />
      </main>
    </div>
  );
}
