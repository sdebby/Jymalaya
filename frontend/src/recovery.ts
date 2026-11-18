// Y ranges for the HRV and Resting HR cards (design system RecoveryTrends).
// Each card has a default range with a label every `step`; when a reading
// leaves it the range stretches in whole steps, so no point is ever clipped and
// the gridlines stay on round numbers.

export interface RecoveryRange {
  min: number;
  max: number;
  step: number;
}

export const HRV_RANGE: RecoveryRange = { min: 40, max: 80, step: 10 };
export const RESTING_HR_RANGE: RecoveryRange = { min: 44, max: 60, step: 4 };

export function recoveryDomain(
  values: (number | null)[],
  def: RecoveryRange,
): { min: number; max: number; ticks: number[] } {
  const xs = values.filter((v): v is number => v != null && Number.isFinite(v));
  let { min, max } = def;
  const { step } = def;
  if (xs.length) {
    while (Math.min(...xs) < min) min -= step;
    while (Math.max(...xs) > max) max += step;
  }
  const ticks: number[] = [];
  for (let t = min; t <= max + 1e-9; t += step) ticks.push(t);
  return { min, max, ticks };
}
