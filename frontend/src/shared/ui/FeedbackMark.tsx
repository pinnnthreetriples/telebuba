import { StatusIcon } from './StatusIcon';

// The design's inline success/error mark for mutation feedback: a green check
// or red cross that pops in next to whatever it's confirming.
export function FeedbackMark({ result }: { result?: 'ok' | 'err' }) {
  if (!result) return null;
  return (
    <span className={`tb-pop inline-flex ${result === 'ok' ? 'text-success' : 'text-danger'}`}>
      <StatusIcon kind={result} />
    </span>
  );
}
