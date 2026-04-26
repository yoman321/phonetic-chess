const tokenKey = (sessionId) => `pc-token:${sessionId}`;

export function getStoredToken(sessionId) {
  return sessionStorage.getItem(tokenKey(sessionId));
}

export function setStoredToken(sessionId, token) {
  sessionStorage.setItem(tokenKey(sessionId), token);
}
