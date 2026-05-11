import { Chessboard as ReactChessboard } from "react-chessboard";
import "./ChessBoard.css";

export default function ChessBoard({
  position,
  turnLabel,
  squareStyles,
  onPieceDrop,
  onSquareClick,
  orientation = "white",
  leftRail = null,
}) {
  return (
    <section className="board-pane">
      <div className="board-row">
        {leftRail}
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
      </div>
      <p className="turn">{turnLabel} to move</p>
    </section>
  );
}
