type Emotion = { valence?: number; arousal?: number; label?: string };

const COLORS: Record<string, string> = {
  excited:   'bg-amber-500/20 text-amber-300 border-amber-600',
  happy:     'bg-green-500/20 text-green-300 border-green-600',
  calm:      'bg-teal-500/20 text-teal-300 border-teal-600',
  sad:       'bg-indigo-500/20 text-indigo-300 border-indigo-600',
  angry:     'bg-red-500/20 text-red-300 border-red-600',
  surprised: 'bg-purple-500/20 text-purple-300 border-purple-600',
  neutral:   'bg-gray-500/20 text-gray-300 border-gray-600',
};

export default function EmotionBadge({ emotion }: { emotion: Emotion }) {
  if (!emotion || Object.keys(emotion).length === 0) return null;

  const label   = emotion.label ?? 'neutral';
  const valence = emotion.valence ?? 0;
  const arousal = emotion.arousal ?? 0;
  const color   = COLORS[label] ?? COLORS.neutral;

  return (
    <div className={`inline-flex flex-col gap-0.5 rounded-lg border px-3 py-1.5 text-xs ${color}`}>
      <span className="font-semibold capitalize">{label}</span>
      <span className="font-mono text-[10px] opacity-70">
        V {valence >= 0 ? '+' : ''}{valence.toFixed(2)}  A {arousal.toFixed(2)}
      </span>
    </div>
  );
}
