export function BrandMark({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand-mark" aria-label="DawAI">
      <span className="brand-symbol" aria-hidden="true">
        <span>D</span>
        <i />
      </span>
      {!compact && <strong>DawAI</strong>}
    </div>
  );
}
