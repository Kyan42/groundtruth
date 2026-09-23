// Runs at most `max` tasks at once; the rest wait in order. In-memory only: queued and running
// work is lost on restart (a real job queue replaces this when deployed).
export function createLimiter(max: number) {
  let active = 0;
  const waiting: (() => void)[] = [];

  return {
    get active() { return active; },
    get queued() { return waiting.length; },
    async run<T>(task: () => Promise<T>): Promise<T> {
      if (active >= max) await new Promise<void>((resolve) => waiting.push(resolve));
      active++;
      try {
        return await task();
      } finally {
        active--;
        waiting.shift()?.();
      }
    },
  };
}
