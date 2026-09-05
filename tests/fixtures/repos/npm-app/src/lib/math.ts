export function clamp(value: number, min: number, max: number): number {
  if (min > max) {
    throw new RangeError("min must be <= max");
  }
  return Math.min(Math.max(value, min), max);
}

export function mean(values: readonly number[]): number {
  if (values.length === 0) {
    return 0;
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}
