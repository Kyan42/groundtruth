import { RunloopSDK } from "@runloop/api-client";

// The few things Groundtruth needs from a sandbox provider. Everything provider-specific stays in
// this file, so switching providers means writing another implementation of Sandbox.

export type CommandResult = { exitCode: number; stdout: string; stderr: string };

export type ExposedPort = {
  url: string;                          // public HTTPS base URL that forwards to the port
  headers: Record<string, string>;      // send on every request, or the tunnel refuses it
};

export interface Sandbox {
  readonly id: string;
  run(command: string, opts?: { timeoutSeconds?: number }): Promise<CommandResult>;
  expose(port: number): Promise<ExposedPort>;
  shutdown(): Promise<void>;
}

export type SandboxOptions = {
  name: string;
  // Safety net: the provider shuts the sandbox down after this long without activity,
  // even if our process dies before calling shutdown().
  idleShutdownSeconds: number;
};

export async function createSandbox(opts: SandboxOptions): Promise<Sandbox> {
  const runloop = new RunloopSDK(); // reads RUNLOOP_API_KEY
  const devbox = await runloop.devbox.create({
    name: opts.name,
    launch_parameters: { after_idle: { idle_time_seconds: opts.idleShutdownSeconds, on_idle: "shutdown" } },
  });

  return {
    id: devbox.id,
    async run(command, { timeoutSeconds = 600 } = {}) {
      const r = await devbox.cmd.exec(command, {}, { longPoll: { timeoutMs: timeoutSeconds * 1000 } });
      return { exitCode: r.exitCode ?? -1, stdout: await r.stdout(), stderr: await r.stderr() };
    },
    async expose(port) {
      const tunnel = await devbox.net.enableTunnel({ auth_mode: "authenticated" });
      if (!tunnel.auth_token) throw new Error("tunnel returned no auth token");
      return {
        url: await devbox.getTunnelUrl(port),
        headers: { "X-Runloop-Tunnel-Authorization": `Bearer ${tunnel.auth_token}` },
      };
    },
    async shutdown() {
      await devbox.shutdown();
    },
  };
}
