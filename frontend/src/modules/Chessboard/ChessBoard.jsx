import { Chessboard as ReactChessboard } from "react-chessboard";
import "./ChessBoard.css";

export default function ChessBoard({
  position,
  turnLabel,
  squareStyles,
  onPieceDrop,
  onSquareClick,
  orientation = "white",
}) {
  return (
    <section className="board-pane">
      <div className="board-wrap">
        <ReactChessboard
          options={{
            position,
            allowDragging: true,
            onPieceDrop,
            onSquareClick,
            squareStyles,
            boardOrientation: orientation,
          }}
        />
      </div>
      <p className="turn">{turnLabel} to move</p>
    </section>
  );
}
