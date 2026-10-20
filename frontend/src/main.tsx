import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { handleRedirect, loadConfig, login } from "./auth";
import "./styles.css";

const root = createRoot(document.getElementById("root")!);

function SignIn() {
  return (
    <div className="signin">
      <h1>Time Tracker</h1>
      <p className="muted">Track time against your projects.</p>
      <button onClick={login}>Sign in or create an account</button>
    </div>
  );
}

(async () => {
  try {
    await loadConfig();
    const signedIn = await handleRedirect();
    root.render(<StrictMode>{signedIn ? <App /> : <SignIn />}</StrictMode>);
  } catch (e) {
    root.render(<div className="signin error">{e instanceof Error ? e.message : String(e)}</div>);
  }
})();
