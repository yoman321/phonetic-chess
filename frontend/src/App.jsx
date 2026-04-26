import { Navigate, Route, Routes } from "react-router-dom";
import Menu from "./modules/Menu/Menu";
import GameView from "./modules/GameView/GameView";
import "./App.css";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Menu />} />
      <Route path="/sessionId/:sessionId" element={<GameView />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
