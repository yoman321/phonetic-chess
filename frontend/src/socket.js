import { io } from "socket.io-client";

let socket = null;

export function getSocket() {
  if (!socket) {
    const url = import.meta.env.VITE_SOCKET_URL;
    if (url === undefined) {
      socket = io("http://127.0.0.1:5001", { autoConnect: false });
    } else if (url === "") {
      socket = io({ autoConnect: false });
    } else {
      socket = io(url, { autoConnect: false });
    }
  }
  return socket;
}
