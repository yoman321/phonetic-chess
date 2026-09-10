import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Chess } from "chess.js";
import ChessBoard from "../Chessboard/ChessBoard";
import Chatbox from "../Chatbox/Chatbox";
import EvalBar from "../EvalBar/EvalBar";
import FatalError from "../FatalError/FatalError";
import { getSession, joinSession, postMove, postSay } from "../../api";
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
  const [evalCp, setEvalCp] = useState(0);
  const [thinkingSide, setThinkingSide] = useState(null);
  const [thinkingStatus, setThinkingStatus] = useState("thinking");
  const [subscriptError, setSubscriptError] = useState(null);
  const [opponentJoined, setOpponentJoined] = useState(false);

  const hasConnectedRef = useRef(false);
  const moveSeqRef = useRef(0);

  const myTurn = color && game.turn() === color[0];

  const applySessionState = useCallback(
    (s) => {
      game.load(s.fen);
      setPosition(game.fen());
      if (typeof s.evalCp === "number") setEvalCp(s.evalCp);
      if (typeof s.both_joined === "boolean") setOpponentJoined(s.both_joined);
    },
    [game],
  );

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
        applySessionState(s);

        const join = await joinSession(sessionId, getStoredToken(sessionId));
        if (cancelled) return;

        setStoredToken(sessionId, join.playerToken);
        setPlayerToken(join.playerToken);
        setColor(join.color);
        if (typeof join.opponentJoined === "boolean") {
          setOpponentJoined(join.opponentJoined);
        }

        let introText;
        if (s.status !== "active") {
          introText = `You're ${join.color}. Game ${s.status.replace("_", " ")}.`;
        } else if (join.color === "white") {
          introText = join.opponentJoined
            ? `You're ${join.color}. Play your first move.`
            : `You're ${join.color}. Waiting for your opponent to join.`;
        } else {
          introText = `You're ${join.color}. Waiting for white's first move.`;
        }
        setMessages([{ from: "system", intro: true, text: introText }]);
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
  }, [sessionId, navigate, game, applySessionState]);

  useEffect(() => {
    if (!color) return;
    const socket = getSocket();

    const onConnect = () => {
      socket.emit("join_session", { sessionId });
      if (hasConnectedRef.current) {
        // reconnect: catch up on moves played while we were gone
        const seq = moveSeqRef.current;
        getSession(sessionId)
          .then((s) => {
            if (!s || seq !== moveSeqRef.current) return; // a move landed mid-fetch
            applySessionState(s);
          })
          .catch(() => {});
      }
      hasConnectedRef.current = true;
    };

    const onMove = ({
      fen,
      san,
      text,
      ply,
      evalCp: cp,
      intent,
      rationale,
      priorTone,
    }) => {
      moveSeqRef.current += 1;
      setThinkingSide(null);
      setThinkingStatus("thinking");
      if (typeof cp === "number") setEvalCp(cp);
      if (game.fen() !== fen) {
        game.load(fen);
        setPosition(fen);
      }
      const mover = ply % 2 === 1 ? "white" : "black";
      setMessages((m) => {
        const next = m.filter((msg) => !msg.intro);
        if (text && mover !== color) {
          next.push({ from: mover, text });
        }
        if (text) {
          const explanation =
            intent || rationale || priorTone
              ? { priorTone: priorTone || "", intent: intent || "", rationale: rationale || "" }
              : undefined;
          next.push({
            from: "system",
            side: mover,
            text: `Move: ${san}`,
            explanation,
          });
        } else if (mover !== color) {
          next.push({ from: "system", side: mover, text: `Opponent → ${san}` });
        }
        return next;
      });
    };

    const onThinking = ({ on, side, status }) => {
      setThinkingSide(on ? side : null);
      setThinkingStatus(status || "thinking");
    };

    // For the player who was already waiting. The joiner is not in the room
    // yet — this effect returns early until `color` is set — and learns from
    // their own join response instead.
    const onPlayerJoined = () => setOpponentJoined(true);

    socket.on("connect", onConnect);
    socket.on("move", onMove);
    socket.on("thinking", onThinking);
    socket.on("player_joined", onPlayerJoined);
    socket.connect();
    if (socket.connected) onConnect(); // already open on remount; connect won't re-fire

    return () => {
      socket.off("connect", onConnect);
      socket.off("move", onMove);
      socket.off("thinking", onThinking);
      socket.off("player_joined", onPlayerJoined);
      hasConnectedRef.current = false; // no spurious resync on a remount
      socket.disconnect();
    };
  }, [sessionId, color, game, applySessionState]);

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

    const pendingId = `pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    setMessages((m) => [...m, { from: color, text, pendingId }]);
    setDraft("");
    setSubscriptError(null);

    postSay(sessionId, text, playerToken).catch((err) => {
      if (err.message === "llm_bad_response") {
        setMessages((m) => m.filter((msg) => msg.pendingId !== pendingId));
        setDraft(text);
        setThinkingSide(null);
        setThinkingStatus("thinking");
        setSubscriptError(
          "llm couldn't understand the message, please retry",
        );
        return;
      }
      if (err.message === "llm_unavailable") {
        setMessages((m) => m.filter((msg) => msg.pendingId !== pendingId));
        setDraft(text);
        setThinkingSide(null);
        setThinkingStatus("thinking");
        setSubscriptError(
          "Groq is unavailable right now, please try again in a moment",
        );
        return;
      }
      if (err.message === "waiting_for_opponent_join") {
        setMessages((m) => [
          ...m.filter((msg) => msg.pendingId !== pendingId),
          { from: "system", text: "Waiting for your opponent to join." },
        ]);
        setDraft(text);
        return;
      }
      setMessages((m) => [
        ...m,
        { from: "system", text: `Phrase rejected: ${err.message}` },
      ]);
    });
  };

  const tryMove = (from, to) => {
    if (!myTurn || !opponentJoined) return null;
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
    if (!targetSquare || !myTurn || !opponentJoined) return false;
    setSelectedSquare(null);
    setLegalTargets([]);
    return tryMove(sourceSquare, targetSquare) !== null;
  };

  const showMovesFor = (square) => {
    if (!myTurn || !opponentJoined) return false;
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
    if (!myTurn || !opponentJoined) return;
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
            leftRail={<EvalBar evalCp={evalCp} orientation={color} />}
          />
        )}
        <Chatbox
          messages={messages}
          draft={draft}
          setDraft={setDraft}
          onSend={handleSend}
          disabled={!myTurn || !!thinkingSide || !opponentJoined}
          thinkingSide={thinkingSide}
          thinkingStatus={thinkingStatus}
          subscriptError={subscriptError}
          myColor={color}
        />
      </main>
    </div>
  );
}
