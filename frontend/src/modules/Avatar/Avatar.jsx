import "./Avatar.css";

const GLYPH = { white: "♔", black: "♚" };

export default function Avatar({ side }) {
  return (
    <div className={`avatar avatar-${side}`}>
      {GLYPH[side]}
    </div>
  );
}
