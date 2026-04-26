const API_BASE = "http://127.0.0.1:5001";

export async function createSession(color) {
  const res = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ color: color ?? "random" }),
  });
  if (!res.ok) throw new Error(`createSession failed: ${res.status}`);
  return res.json();
}

export async function getSession(sessionId) {
  const res = await fetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`getSession failed: ${res.status}`);
  return res.json();
}

export async function joinSession(sessionId, playerToken) {
  const res = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/join`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ playerToken: playerToken ?? null }),
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `joinSession failed: ${res.status}`);
  }
  return res.json();
}

export async function postMove(sessionId, uci, playerToken) {
  const res = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/move`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uci, playerToken }),
    },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `postMove failed: ${res.status}`);
  }
  return res.json();
}
