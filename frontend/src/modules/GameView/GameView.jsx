import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Chess } from "chess.js";
import ChessBoard from "../Chessboard/ChessBoard";
import Chatbox from "../Chatbox/Chatbox";
import FatalError from "../FatalError/FatalError";
import { getSession, joinSession, postMove } from "../../api";
import { getStoredToken, setStoredToken } from "../../storage";
import { getSocket } from "../../socket";
import "./GameView.css";

export default function GameView() {
  const { sessionId } = useParams();
  const navigate = useNavigate();

  const [validating, setValidating] = useState(true);
  const [fatalError, setFatalError] = useState(null);
  const [color, setColor] = useState(null);
  const [playerToken, setPlayerToken] = useState(null);
  const [game] = useState(() => new Chess());
  const [position, setPosition] = useState(() => game.fen());
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [selectedSquare, setSelectedSquare] = useState(null);
  const [legalTargets, setLegalTargets] = useState([]);

  const myTurn = color && game.turn() === color[0];

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const s = await getSession(sessionId);
        if (cancelled) return;
        if (!s) {
          navigate("/", { replace: true });
          return;
        }
        game.load(s.fen);
        setPosition(game.fen());

        const join = await joinSession(sessionId, getStoredToken(sessionId));
        if (cancelled) return;

        setStoredToken(sessionId, join.playerToken);
        setPlayerToken(join.playerToken);
        setColor(join.color);

        setMessages([
          {
            from: "system",
            text: `You are ${join.color}. ${
              s.status === "active"
                ? "Waiting for your turn."
                : `Game ${s.status.replace("_", " ")}.`
            }`,
          },
        ]);
        setValidating(false);
      } catch (e) {
        if (!cancelled) {
          const msg =
            e.message === "session_full"
              ? "This game is already full."
              : `Error: ${e.message}`;
          setFatalError(msg);
          setValidating(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [sessionId, navigate, game]);

  useEffect(() => {
    if (!color) return;
    const socket = getSocket();
    socket.connect();
    socket.emit("join_session", { sessionId });

    const onMove = ({ fen, san }) => {
      if (game.fen() === fen) return;
      game.load(fen);
      setPosition(fen);
      setMessages((m) => [...m, { from: "system", text: `Opponent → ${san}` }]);
    };
    socket.on("move", onMove);

    return () => {
      socket.off("move", onMove);
      socket.disconnect();
    };
  }, [sessionId, color, game]);

  const turnLabel = position && game.turn() === "w" ? "White" : "Black";

  const persistMove = (move) => {
    const uci = `${move.from}${move.to}${move.promotion ?? ""}`;
    postMove(sessionId, uci, playerToken).catch((e) => {
      game.undo();
      setPosition(game.fen());
      setMessages((m) => [
        ...m,
        { from: "system", text: `Move rejected: ${e.message}` },
      ]);
    });
  };

  const handleSend = (e) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;

    setMessages((m) => [...m, { from: "user", text }]);
    setDraft("");
  };

  const tryMove = (from, to) => {
    if (!myTurn) return null;
    try {
      const move = game.move({ from, to, promotion: "q" });
      if (!move) return null;
      setPosition(game.fen());
      setMessages((m) => [...m, { from: color, text: move.san }]);
      persistMove(move);
      return move;
    } catch {
      return null;
    }
  };

  const handlePieceDrop = ({ sourceSquare, targetSquare }) => {
    if (!targetSquare || !myTurn) return false;
    setSelectedSquare(null);
    setLegalTargets([]);
    return tryMove(sourceSquare, targetSquare) !== null;
  };

  const showMovesFor = (square) => {
    if (!myTurn) return false;
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
    if (!myTurn) return;
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

  if (validating) {
    return (
      <div className="app">
        <main className="layout">
          <p className="muted">Loading session…</p>
        </main>
      </div>
    );
  }

  if (fatalError) {
    return (
      <div className="app">
        <main className="layout">
          <FatalError message={fatalError} onBack={() => navigate("/")} />
        </main>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Phonetic Chess</h1>
        <div className="header-right">
          <span className="session-tag">
            Session: <code>{sessionId}</code> · You: {color} · Turn: {turnLabel}
          </span>
          <button onClick={() => navigate("/")} className="reset-btn">Leave</button>
        </div>
      </header>

      <main className="layout">
        {color && (
          <ChessBoard
            key={color}
            position={position}
            turnLabel={turnLabel}
            squareStyles={squareStyles}
            onPieceDrop={handlePieceDrop}
            onSquareClick={handleSquareClick}
            orientation={color}
          />
        )}
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
