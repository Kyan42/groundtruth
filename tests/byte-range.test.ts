import { describe, expect, it } from "vitest";

import { InvalidByteRangeError, parseByteRange } from "@/lib/http/byte-range";

describe("parseByteRange", () => {
  it.each([
    [null, undefined],
    ["bytes=0-1023", "bytes=0-1023"],
    ["bytes=1024-", "bytes=1024-"],
    ["bytes=-512", "bytes=-512"],
    [" Bytes = 0001-0002 ", "bytes=1-2"],
  ])("normalizes %s", (value, expected) => {
    expect(parseByteRange(value)).toBe(expected);
  });

  it.each([
    "",
    "items=0-1",
    "bytes=-",
    "bytes=10-2",
    "bytes=0-1,4-5",
    `bytes=0-${Number.MAX_SAFE_INTEGER}0`,
  ])("rejects invalid range %s", (value) => {
    expect(() => parseByteRange(value)).toThrow(InvalidByteRangeError);
  });
});
