import "./FatalError.css";

export default function FatalError({ title = "Can't join session", message, onBack }) {
  return (
    <div className="session-error">
      <h2>{title}</h2>
      <p>{message}</p>
      <button className="menu-btn primary" onClick={onBack}>
        Back to menu
      </button>
    </div>
  );
}
