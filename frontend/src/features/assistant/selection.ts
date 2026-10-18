export interface Fragment {
  start: number;
  end: number;
  body: string;
}
// Textarea offsets count UTF-16 units; the Python API counts Unicode code points.
export function capture(
  body: string,
  start: number,
  end: number,
): Fragment | null {
  return end > start
    ? {
        body,
        start: Array.from(body.slice(0, start)).length,
        end: Array.from(body.slice(0, end)).length,
      }
    : null;
}
export function rebase(fragment: Fragment, body: string): Fragment | null {
  if (fragment.body === body) return fragment;
  const before = Array.from(fragment.body),
    after = Array.from(body);
  let prefix = 0,
    suffix = 0;
  while (
    prefix < Math.min(before.length, after.length) &&
    before[prefix] === after[prefix]
  )
    prefix++;
  while (
    suffix < Math.min(before.length, after.length) - prefix &&
    before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  )
    suffix++;
  if (before.length - suffix <= fragment.start) {
    const shift = after.length - before.length;
    return { body, start: fragment.start + shift, end: fragment.end + shift };
  }
  if (prefix >= fragment.end) return { ...fragment, body };
  return null;
}
