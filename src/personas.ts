// Test accounts the agent logs in as. The agent sees each persona's name, description and username, and
// types the password as a placeholder ({{admin.password}}); the browser swaps in the real value as it
// types, and anything read back from the page shows the placeholder again. So the real password never
// reaches the model, the trace, the dashboard or the compiled tests (they call persona("admin").password).

export type Persona = { name: string; description: string; username: string; password: string };

export const placeholder = (name: string) => `{{${name}.password}}`;
const PLACEHOLDER = /\{\{([\w-]+)\.password\}\}/g;

// The value to type: placeholders replaced by the real passwords.
export function fillPasswords(value: string, personas: Persona[]): string {
  return value.replace(PLACEHOLDER, (whole, name) => {
    const p = personas.find((x) => x.name === name);
    if (!p) throw new Error(`No persona "${name}"; the test accounts are: ${personas.map((x) => x.name).join(", ") || "none"}`);
    return p.password;
  });
}

// Text read from the page (snapshots, observed values, the trace): real passwords replaced by placeholders.
export function hidePasswords(text: string, personas: Persona[]): string {
  return personas.reduce((acc, p) => (p.password ? acc.split(p.password).join(placeholder(p.name)) : acc), text);
}

// What the agent is told about the test accounts.
export function describePersonas(personas: Persona[]): string {
  if (!personas.length) return "";
  return "Test accounts (log in through the app's own sign-in page when a claim needs a signed-in user; " +
    "pick the account whose description fits the claim):\n" +
    personas.map((p) => `- ${p.name}: ${p.description}. Username: ${p.username}. Password: type exactly ${placeholder(p.name)}`).join("\n") +
    "\nThe real password is filled in as you type the placeholder, and snapshots show the placeholder, not the password.";
}

// Shell exports for commands that create the accounts: GT_PERSONA_ADMIN_USERNAME, GT_PERSONA_ADMIN_PASSWORD, ...
export function personaExports(personas: Persona[]): string {
  const q = (v: string) => `'${v.replace(/'/g, "'\\''")}'`;
  return personas.map((p) => {
    const key = p.name.toUpperCase().replace(/[^A-Z0-9]/g, "_");
    return `export GT_PERSONA_${key}_USERNAME=${q(p.username)} GT_PERSONA_${key}_PASSWORD=${q(p.password)}; `;
  }).join("");
}
