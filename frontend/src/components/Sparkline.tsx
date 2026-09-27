interface Props {
  values: number[];
  color: string;
}

/** Спарклайн вероятности задержки с порогами 50% и 70% */
export function Sparkline({ values, color }: Props) {
  const W = 320, H = 48;
  if (values.length < 2) return null;
  const x = (i: number) => (i / (values.length - 1)) * W;
  const y = (v: number) => H - 4 - v * (H - 8);
  const line = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join('');
  const last = values[values.length - 1];
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <line x1="0" x2={W} y1={y(0.7)} y2={y(0.7)} className="spark__th spark__th--bad" />
      <line x1="0" x2={W} y1={y(0.5)} y2={y(0.5)} className="spark__th spark__th--warn" />
      <path d={`${line}L${W} ${H}L0 ${H}Z`} fill={color} fillOpacity={0.14} />
      <path d={line} fill="none" stroke={color} strokeWidth={2} vectorEffect="non-scaling-stroke" />
      <circle cx={W - 3} cy={y(last)} r={3} fill={color} />
    </svg>
  );
}
