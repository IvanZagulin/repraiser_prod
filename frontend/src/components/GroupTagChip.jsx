export default function GroupTagChip({ name, color }) {
  const bg = color ? `${color}20` : 'rgba(107,114,128,0.12)';
  const dot = color || '#6b7280';

  return (
    <span
      className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded-md text-foreground"
      style={{ backgroundColor: bg }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full shrink-0"
        style={{ backgroundColor: dot }}
      />
      {name}
    </span>
  );
}
