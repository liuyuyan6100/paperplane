// Align whole rendered blocks, so lists, code and tables retain their formatting.
// Bound quadratic work for very long articles; the fallback marks the middle
// conservatively instead of dropping any of the article from the reader.
export function changedBlocks(before: string[], after: string[]) {
  const left = new Set(before.map((_, i) => i));
  const right = new Set(after.map((_, i) => i));
  const approximate = before.length * after.length > 250000;
  if (approximate) {
    let start = 0;
    while (
      start < before.length &&
      start < after.length &&
      before[start] === after[start]
    ) {
      left.delete(start);
      right.delete(start);
      start++;
    }
    let a = before.length - 1,
      b = after.length - 1;
    while (a >= start && b >= start && before[a] === after[b]) {
      left.delete(a--);
      right.delete(b--);
    }
  } else {
    const rows = Array.from(
      { length: before.length + 1 },
      () => new Uint32Array(after.length + 1),
    );
    for (let a = before.length - 1; a >= 0; a--)
      for (let b = after.length - 1; b >= 0; b--)
        rows[a][b] =
          before[a] === after[b]
            ? rows[a + 1][b + 1] + 1
            : Math.max(rows[a + 1][b], rows[a][b + 1]);
    let a = 0,
      b = 0;
    while (a < before.length && b < after.length) {
      if (before[a] === after[b]) {
        left.delete(a++);
        right.delete(b++);
      } else if (rows[a + 1][b] >= rows[a][b + 1]) a++;
      else b++;
    }
  }
  return { left, right, approximate };
}
