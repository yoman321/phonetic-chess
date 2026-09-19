/* Phase 3 gate — the explanation request, and only it.
 *
 * `explainMove` is the one new call this plan adds. It is gated on the wire
 * shape rather than on the component, because the component gates below would
 * pass just as well against a call that posted to the wrong path.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { explainMove } from "./api";

const SESSION_ID = "abc12345";
const TOKEN = "tok-white";

let fetchMock;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

function answers(status, body) {
  fetchMock.mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}

describe("explainMove", () => {
  it("posts the player token to the move's explain route", async () => {
    answers(200, { intent: "an intent", rationale: "a why" });

    const out = await explainMove(SESSION_ID, 3, TOKEN);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toMatch(
      new RegExp(`/sessions/${SESSION_ID}/moves/3/explain$`),
    );
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ playerToken: TOKEN });
    expect(out).toEqual({ intent: "an intent", rationale: "a why" });
  });

  it("escapes the session id", async () => {
    answers(200, {});
    await explainMove("a/b", 1, TOKEN);
    expect(fetchMock.mock.calls[0][0]).toContain("a%2Fb");
  });

  it("throws the server's error code, which is what the card shows", async () => {
    answers(502, { error: "llm_unavailable" });
    await expect(explainMove(SESSION_ID, 1, TOKEN)).rejects.toThrow(
      "llm_unavailable",
    );
  });

  it("still throws when the body is not JSON", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new Error("not json");
      },
    });
    await expect(explainMove(SESSION_ID, 1, TOKEN)).rejects.toThrow(/500/);
  });
});
