"use strict";

// Shared Google sign-in control for the VoiceCal pages. Renders into #account
// and tells the page (onChange) whether the server is up and who is signed in.

window.VoiceCalAccount = (() => {
  const state = { online: null, configured: false, signedIn: false, email: "", health: null };
  const listeners = [];

  function useIcon(name, extraClass = "") {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", `icon ${extraClass}`.trim());
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function pill(text, tone, title) {
    const el = document.createElement("span");
    el.className = "status-pill";
    el.dataset.state = tone;
    el.textContent = text;
    if (title) el.title = title;
    return el;
  }

  function loginUrl() {
    return `/auth/login?next=${encodeURIComponent(window.location.pathname)}`;
  }

  function render() {
    const root = document.getElementById("account");
    if (!root) return;

    if (state.online === false) {
      root.replaceChildren(pill("Server offline", "error", "Start it with: uvicorn demo:app --reload"));
      return;
    }
    if (!state.configured) {
      root.replaceChildren(pill("Google sign-in not set up", "warn",
        "Put credentials.json in the CodeJam-Backend folder."));
      return;
    }
    if (!state.signedIn) {
      const link = document.createElement("a");
      link.className = "btn btn-google btn-small";
      link.href = loginUrl();
      const label = document.createElement("span");
      label.innerHTML = 'Sign in<span class="google-label-long"> with Google</span>';
      link.append(useIcon("google", "icon-google"), label);
      root.replaceChildren(link);
      return;
    }

    const chip = document.createElement("span");
    chip.className = "account-chip";
    chip.title = `Signed in as ${state.email || "your Google account"}`;
    const avatar = document.createElement("span");
    avatar.className = "avatar";
    avatar.textContent = (state.email || "?").charAt(0).toUpperCase();
    const email = document.createElement("span");
    email.className = "account-email";
    email.textContent = state.email || "Signed in";
    chip.append(avatar, email);

    const signOutBtn = document.createElement("button");
    signOutBtn.type = "button";
    signOutBtn.className = "icon-btn";
    signOutBtn.title = "Sign out";
    signOutBtn.setAttribute("aria-label", "Sign out");
    signOutBtn.append(useIcon("logout"));
    signOutBtn.addEventListener("click", signOut);

    root.replaceChildren(chip, signOutBtn);
  }

  function emit() {
    render();
    listeners.forEach((fn) => fn(state));
  }

  async function refresh() {
    try {
      const res = await fetch("/api/health", { credentials: "same-origin" });
      if (!res.ok) throw new Error(res.statusText);
      const health = await res.json();
      Object.assign(state, {
        online: true,
        configured: Boolean(health.calendar_configured),
        signedIn: Boolean(health.signed_in),
        email: health.email || "",
        health,
      });
    } catch {
      Object.assign(state, { online: false, signedIn: false, email: "", health: null });
    }
    emit();
    return state;
  }

  async function signOut() {
    try {
      await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
    } catch {
      // the server will treat the old session as gone once it is reachable again
    }
    await refresh();
  }

  function signIn() {
    window.location.href = loginUrl();
  }

  const ready = refresh();
  return { state, ready, refresh, signIn, signOut, onChange: (fn) => listeners.push(fn) };
})();
