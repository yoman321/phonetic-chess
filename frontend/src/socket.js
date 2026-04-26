import { io } from "socket.io-client";

let socket = null;

export function getSocket() {
  if (!socket) {
    socket = io("http://127.0.0.1:5001", { autoConnect: false });
  }
  return socket;
}
