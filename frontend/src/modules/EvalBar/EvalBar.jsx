import "./EvalBar.css";

const CLAMP_CP = 1000;

export default function EvalBar({ evalCp, orientation = "white" }) {
  const cp = Number.isFinite(evalCp) ? evalCp : 0;
  const clamped = Math.max(-CLAMP_CP, Math.min(CLAMP_CP, cp));
  const whitePct = 50 + (clamped / CLAMP_CP) * 50;

  const pawns = (cp / 100).toFixed(1);
  const label = cp > 0 ? `+${pawns}` : pawns;

  return (
    <div className="eval-bar" data-orientation={orientation}>
      <div className="eval-fill eval-black" style={{ height: `${100 - whitePct}%` }} />
      <div className="eval-fill eval-white" style={{ height: `${whitePct}%` }} />
      <div className="eval-label">{label}</div>
    </div>
  );
}
