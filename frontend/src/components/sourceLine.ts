const WINDOW = 10;

function wordsOf(text: string) {
  return new Set(text.toLowerCase().match(/[\p{L}\p{N}+#]{3,}/gu) ?? []);
}

/**
 * SyncTeX reports where TeX finished reading, which can be lines after the text when a macro's
 * arguments span several lines. The clicked text is near there, so the nearby line with most of
 * its words is the better answer; LaTeX commands in the source only cost a few of them.
 */
export function refineLine(source: string, line: number, text: string) {
  const words = wordsOf(text);
  const lines = source.split("\n");
  let best = line;
  let bestScore = 0;
  for (let row = 1; row <= lines.length; row++) {
    if (Math.abs(row - line) > WINDOW) continue;
    const present = wordsOf(lines[row - 1]);
    const score = [...words].filter((word) => present.has(word)).length;
    const closer = Math.abs(row - line) < Math.abs(best - line);
    if (score > bestScore || (score === bestScore && closer)) {
      best = row;
      bestScore = score;
    }
  }
  return bestScore * 2 >= words.size && bestScore > 0 ? best : line;
}
