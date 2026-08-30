export class InvalidByteRangeError extends Error {
  constructor() {
    super("Range must be a single byte range.");
    this.name = "InvalidByteRangeError";
  }
}

export function parseByteRange(value: string | null): string | undefined {
  if (value === null) {
    return undefined;
  }

  const match = /^\s*bytes\s*=\s*(\d*)-(\d*)\s*$/i.exec(value);
  if (!match || (!match[1] && !match[2])) {
    throw new InvalidByteRangeError();
  }

  const start = parsePart(match[1]);
  const end = parsePart(match[2]);
  if (start !== undefined && end !== undefined && start > end) {
    throw new InvalidByteRangeError();
  }

  return `bytes=${start ?? ""}-${end ?? ""}`;
}

function parsePart(value: string): number | undefined {
  if (!value) {
    return undefined;
  }

  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed)) {
    throw new InvalidByteRangeError();
  }
  return parsed;
}
