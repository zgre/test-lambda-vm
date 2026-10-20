// Cognito Hosted UI login using the OAuth authorization-code flow with PKCE.

export interface Config {
  apiUrl: string;
  region: string;
  userPoolClientId: string;
  authDomain: string;
}

interface Tokens {
  accessToken: string;
  idToken: string;
  refreshToken?: string;
  expiresAt: number; // epoch ms
}

const TOKENS_KEY = "tt.tokens";
const VERIFIER_KEY = "tt.pkce";

let config: Config;
let tokens: Tokens | null = load();

export async function loadConfig(): Promise<Config> {
  const res = await fetch("/config.json", { cache: "no-store" });
  if (!res.ok) throw new Error(`Could not load /config.json (${res.status})`);
  config = await res.json();
  return config;
}

export const getConfig = () => config;

const redirectUri = () => `${window.location.origin}/`;

function load(): Tokens | null {
  try {
    const raw = localStorage.getItem(TOKENS_KEY);
    return raw ? (JSON.parse(raw) as Tokens) : null;
  } catch {
    return null;
  }
}

function save(t: Tokens | null) {
  tokens = t;
  try {
    if (t) localStorage.setItem(TOKENS_KEY, JSON.stringify(t));
    else localStorage.removeItem(TOKENS_KEY);
  } catch {
    // Storage unavailable: tokens live in memory for this tab only.
  }
}

function base64url(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  return btoa(String.fromCharCode(...arr)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export async function login() {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = base64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  sessionStorage.setItem(VERIFIER_KEY, verifier);
  const params = new URLSearchParams({
    response_type: "code",
    client_id: config.userPoolClientId,
    redirect_uri: redirectUri(),
    scope: "openid email",
    code_challenge_method: "S256",
    code_challenge: challenge,
  });
  window.location.assign(`${config.authDomain}/oauth2/authorize?${params}`);
}

export function logout() {
  save(null);
  const params = new URLSearchParams({ client_id: config.userPoolClientId, logout_uri: redirectUri() });
  window.location.assign(`${config.authDomain}/logout?${params}`);
}

async function tokenRequest(body: Record<string, string>): Promise<Tokens> {
  const res = await fetch(`${config.authDomain}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: config.userPoolClientId, ...body }),
  });
  if (!res.ok) throw new Error(`Token request failed (${res.status})`);
  const data = await res.json();
  return {
    accessToken: data.access_token,
    idToken: data.id_token,
    refreshToken: data.refresh_token ?? body.refresh_token,
    expiresAt: Date.now() + data.expires_in * 1000,
  };
}

/** Finishes a login redirect if the URL carries ?code=. Returns true when signed in. */
export async function handleRedirect(): Promise<boolean> {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("code");
  if (code) {
    const verifier = sessionStorage.getItem(VERIFIER_KEY) ?? "";
    sessionStorage.removeItem(VERIFIER_KEY);
    window.history.replaceState({}, "", url.pathname);
    save(await tokenRequest({ grant_type: "authorization_code", code, redirect_uri: redirectUri(), code_verifier: verifier }));
  }
  return tokens !== null;
}

export async function accessToken(): Promise<string> {
  if (!tokens) throw new Error("Not signed in");
  if (Date.now() > tokens.expiresAt - 60_000) {
    if (!tokens.refreshToken) {
      save(null);
      throw new Error("Session expired");
    }
    try {
      save(await tokenRequest({ grant_type: "refresh_token", refresh_token: tokens.refreshToken }));
    } catch (e) {
      save(null);
      throw e;
    }
  }
  return tokens!.accessToken;
}

export function userEmail(): string | null {
  if (!tokens) return null;
  try {
    const payload = JSON.parse(atob(tokens.idToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload.email ?? null;
  } catch {
    return null;
  }
}
