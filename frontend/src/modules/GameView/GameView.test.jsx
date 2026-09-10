import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Chess } from "chess.js";

// getSocket and the api module are replaced so the component's socket
// behaviour can be driven by hand — no server, no backend.
const socketRef = vi.hoisted(() => ({ current: null }));
const api = vi.hoisted(() => ({ current: {} }));

vi.mock("../../socket", () => ({ getSocket: () => socketRef.current }));
vi.mock("../../api", () => ({
  getSession: (...a) => api.current.getSession(...a),
  joinSession: (...a) => api.current.joinSession(...a),
  postMove: (...a) => api.current.postMove(...a),
  postSay: (...a) => api.current.postSay(...a),
}));
// react-chessboard needs a real layout to render; the board's position is the
// only thing these tests read, so stand in for it with an element that carries
// the exact FEN string the component computed.
vi.mock("../Chessboard/ChessBoard", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ position }) =>
      createElement("div", {
        "data-testid": "board",
        "data-position": position,
      }),
  };
});

import GameView from "./GameView";

const SESSION_ID = "abc12345";
const START_FEN = new Chess().fen();

/** FEN after 1.e4 e5 2.Nf3 — built with the client's own chess.js so the
 * comparison is exact rather than dependent on FEN normalisation. */
const FEN_AFTER_THREE_MOVES = (() => {
  const g = new Chess();
  g.move("e4");
  g.move("e5");
  g.move("Nf3");
  return g.fen();
})();

/** Stands in for socket.io-client. Models the part that matters: connect() only
 * asks the transport to open — `connected` stays false and no "connect" event
 * fires until the server answers, which the test does explicitly. */
class FakeSocket {
  constructor() {
    this.connected = false;
    this.connectCalls = 0;
    this.emits = [];
    this.handlers = new Map();
  }

  on(event, fn) {
    if (!this.handlers.has(event)) this.handlers.set(event, []);
    this.handlers.get(event).push(fn);
  }

  off(event, fn) {
    const list = this.handlers.get(event) ?? [];
    const i = list.indexOf(fn);
    if (i !== -1) list.splice(i, 1);
  }

  emit(event, payload) {
    this.emits.push({ event, payload });
  }

  connect() {
    this.connectCalls += 1;
  }

  disconnect() {
    this.serverClose("io client disconnect");
  }

  // --- test-side drivers ---

  fire(event, payload) {
    for (const fn of [...(this.handlers.get(event) ?? [])]) fn(payload);
  }

  serverConnect() {
    this.connected = true;
    this.fire("connect");
  }

  serverClose(reason = "transport close") {
    this.connected = false;
    this.fire("disconnect", reason);
  }

  emitCount(event) {
    return this.emits.filter((e) => e.event === event).length;
  }
}

function renderGame() {
  return render(
    <MemoryRouter initialEntries={[`/sessionId/${SESSION_ID}`]}>
      <Routes>
        <Route path="/sessionId/:sessionId" element={<GameView />} />
      </Routes>
    </MemoryRouter>,
  );
}

const boardPosition = () =>
  screen.getByTestId("board").getAttribute("data-position");

/** Wait until the join has resolved and the socket effect has run. */
async function waitForSocketEffect(socket) {
  await waitFor(() => expect(socket.connectCalls).toBe(1));
}

let socket;

beforeEach(() => {
  socket = new FakeSocket();
  socketRef.current = socket;
  api.current = {
    getSession: vi.fn().mockResolvedValue({
      id: SESSION_ID,
      fen: START_FEN,
      pgn: "",
      status: "active",
      evalCp: 0,
    }),
    joinSession: vi
      .fn()
      .mockResolvedValue({ color: "white", playerToken: "tok-white" }),
    postMove: vi.fn().mockResolvedValue({}),
    postSay: vi.fn().mockResolvedValue({}),
  };
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("invariant 1 — rejoin", () => {
  it("emits join_session on every connect", async () => {
    renderGame();
    await waitForSocketEffect(socket);

    await act(async () => socket.serverConnect());
    await act(async () => socket.serverClose());
    await act(async () => socket.serverConnect());

    expect(socket.emitCount("join_session")).toBe(2);
  });

  it("names the session it is rejoining", async () => {
    renderGame();
    await waitForSocketEffect(socket);
    await act(async () => socket.serverConnect());

    const joins = socket.emits.filter((e) => e.event === "join_session");
    expect(joins.at(-1).payload).toEqual({ sessionId: SESSION_ID });
  });
});

describe("invariant 3 — resync", () => {
  it("adopts the server position after a reconnect gap", async () => {
    api.current.getSession = vi
      .fn()
      .mockResolvedValueOnce({
        id: SESSION_ID,
        fen: START_FEN,
        pgn: "",
        status: "active",
        evalCp: 0,
      })
      .mockResolvedValueOnce({
        id: SESSION_ID,
        fen: FEN_AFTER_THREE_MOVES,
        pgn: "1. e4 e5 2. Nf3",
        status: "active",
        evalCp: 42,
      });

    renderGame();
    await waitForSocketEffect(socket);
    await act(async () => socket.serverConnect());
    expect(boardPosition()).toBe(START_FEN);

    await act(async () => socket.serverClose());
    await act(async () => socket.serverConnect());

    await waitFor(() =>
      expect(boardPosition()).toBe(FEN_AFTER_THREE_MOVES),
    );
  });

  it("keeps a move that lands while the resync fetch is in flight", async () => {
    // Regression guard on the Phase 1 change rather than a gate: today there is
    // no refetch, so nothing can overwrite the move. Once the refetch exists, a
    // broadcast arriving mid-fetch must win over the older fetched FEN — the
    // move is never re-sent, so losing it desyncs the board permanently.
    let resolveRefetch;
    api.current.getSession = vi
      .fn()
      .mockResolvedValueOnce({
        id: SESSION_ID,
        fen: START_FEN,
        pgn: "",
        status: "active",
        evalCp: 0,
      })
      .mockImplementationOnce(
        () => new Promise((res) => { resolveRefetch = res; }),
      );

    renderGame();
    await waitForSocketEffect(socket);
    await act(async () => socket.serverConnect());
    await act(async () => socket.serverClose());
    await act(async () => socket.serverConnect());

    // The move lands first...
    await act(async () => {
      socket.fire("move", {
        fen: FEN_AFTER_THREE_MOVES,
        san: "Nf3",
        ply: 3,
        evalCp: 42,
      });
    });
    // ...then the stale fetch answers with the pre-move position.
    await act(async () => {
      resolveRefetch?.({
        id: SESSION_ID,
        fen: START_FEN,
        pgn: "",
        status: "active",
        evalCp: 0,
      });
    });

    expect(boardPosition()).toBe(FEN_AFTER_THREE_MOVES);
  });
});
