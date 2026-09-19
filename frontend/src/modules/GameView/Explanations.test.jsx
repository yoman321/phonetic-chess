/* Phase 3 gates — invariants 5, 6, 8 and 9 from the client's side.
 *
 * Driven through GameView, not Chatbox, because the explanation needs the
 * session id, the player token and the message it belongs to, and which
 * component holds each of those is an implementation choice the plan leaves
 * open. What must hold is what a player sees and how many requests it costs.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { Chess } from "chess.js";

const socketRef = vi.hoisted(() => ({ current: null }));
const api = vi.hoisted(() => ({ current: {} }));

vi.mock("../../socket", () => ({ getSocket: () => socketRef.current }));
vi.mock("../../api", () => ({
  getSession: (...a) => api.current.getSession(...a),
  joinSession: (...a) => api.current.joinSession(...a),
  postMove: (...a) => api.current.postMove(...a),
  postSay: (...a) => api.current.postSay(...a),
  explainMove: (...a) => api.current.explainMove(...a),
}));
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
const TOKEN = "tok-white";
const START_FEN = new Chess().fen();
const FEN_AFTER_E4 = (() => {
  const g = new Chess();
  g.move("e4");
  return g.fen();
})();

const INTENT = "The player is announcing an all-out attack.";
const RATIONALE = "Pushing the king's pawn seizes the centre at once.";

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
    this.connected = false;
    this.fire("disconnect", "io client disconnect");
  }
  fire(event, payload) {
    for (const fn of [...(this.handlers.get(event) ?? [])]) fn(payload);
  }
  serverConnect() {
    this.connected = true;
    this.fire("connect");
  }
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
      .mockResolvedValue({ color: "white", playerToken: TOKEN }),
    postMove: vi.fn().mockResolvedValue({}),
    postSay: vi.fn().mockResolvedValue({}),
    explainMove: vi.fn(),
  };
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

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

/** Play one LLM move over the socket, in the shape Phase 1 leaves it: a ply, a
 * message and a tone summary, and no inline explanation. */
async function playLlmMove({ ply = 1, priorTone = "" } = {}) {
  renderGame();
  await waitFor(() => expect(socket.connectCalls).toBe(1));
  await act(async () => socket.serverConnect());
  await act(async () => {
    socket.fire("move", {
      fen: FEN_AFTER_E4,
      san: "e4",
      uci: "e2e4",
      ply,
      text: "come out swinging",
      tone_summary: "fired up",
      priorTone,
      evalCp: 80,
    });
  });
}

const askButton = () => screen.getByRole("button", { name: /why this move/i });

async function press() {
  await act(async () => {
    askButton().click();
  });
}

describe("invariant 9 — the panel exists without a fetch", () => {
  it("offers `?` on an LLM move that carries no prior tone", async () => {
    await playLlmMove({ priorTone: "" });
    expect(askButton()).toBeTruthy();
    expect(api.current.explainMove).not.toHaveBeenCalled();
  });

  it("renders the prior tone straight from the move payload", async () => {
    // Left pending: the prior tone must be on screen before any answer is.
    api.current.explainMove = vi.fn(() => new Promise(() => {}));
    await playLlmMove({ priorTone: "calm and watchful" });
    await press();

    expect(screen.getByText(/calm and watchful/)).toBeTruthy();
    expect(screen.queryByText(INTENT)).toBeNull();
  });
});

describe("invariant 5 — the first press generates", () => {
  it("asks the server for this move's explanation and shows it", async () => {
    api.current.explainMove = vi
      .fn()
      .mockResolvedValue({ intent: INTENT, rationale: RATIONALE });
    await playLlmMove({ ply: 1 });

    await press();

    await waitFor(() => expect(screen.getByText(INTENT)).toBeTruthy());
    expect(screen.getByText(RATIONALE)).toBeTruthy();
    expect(api.current.explainMove).toHaveBeenCalledTimes(1);
    expect(api.current.explainMove).toHaveBeenCalledWith(SESSION_ID, 1, TOKEN);
  });

  it("asks for the ply of the move that was pressed", async () => {
    api.current.explainMove = vi
      .fn()
      .mockResolvedValue({ intent: INTENT, rationale: RATIONALE });
    await playLlmMove({ ply: 7 });

    await press();

    await waitFor(() =>
      expect(api.current.explainMove).toHaveBeenCalledWith(SESSION_ID, 7, TOKEN),
    );
  });
});

describe("invariant 6 — a reopen costs nothing", () => {
  it("makes no second request after closing and reopening", async () => {
    api.current.explainMove = vi
      .fn()
      .mockResolvedValue({ intent: INTENT, rationale: RATIONALE });
    await playLlmMove();

    await press();
    await waitFor(() => expect(screen.getByText(INTENT)).toBeTruthy());
    await press();                       // close
    expect(screen.queryByText(INTENT)).toBeNull();
    await press();                       // reopen

    expect(screen.getByText(INTENT)).toBeTruthy();
    expect(api.current.explainMove).toHaveBeenCalledTimes(1);
  });
});

describe("invariant 8 — a failure stays in the card", () => {
  it("shows the error and leaves the board and the history alone", async () => {
    api.current.explainMove = vi
      .fn()
      .mockRejectedValue(new Error("llm_unavailable"));
    await playLlmMove();
    const before = boardPosition();

    await press();

    await waitFor(() => expect(screen.getByText(/llm_unavailable/)).toBeTruthy());
    expect(boardPosition()).toBe(before);
    expect(screen.getByText("Move: e4")).toBeTruthy();
    expect(api.current.postMove).not.toHaveBeenCalled();
    expect(api.current.postSay).not.toHaveBeenCalled();
  });

  it("lets the next press try again", async () => {
    api.current.explainMove = vi
      .fn()
      .mockRejectedValueOnce(new Error("llm_unavailable"))
      .mockResolvedValueOnce({ intent: INTENT, rationale: RATIONALE });
    await playLlmMove();

    await press();
    await waitFor(() => expect(screen.getByText(/llm_unavailable/)).toBeTruthy());
    await press();                       // close
    await press();                       // try again

    await waitFor(() => expect(screen.getByText(INTENT)).toBeTruthy());
    expect(api.current.explainMove).toHaveBeenCalledTimes(2);
  });
});
