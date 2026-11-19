// E2E-only fake identity; never bundled by the production Vite config.
export class Clerk {
  constructor(_publishableKey) {
    this.session = { getToken: async () => "only-e2e-mocked-jwt" };
    this.isSignedIn = true;
  }
  async load() {}
  addListener(callback) { this.listener = callback; callback(); }
  mountSignIn() {}
  unmountSignIn() {}
  async signOut() {
    this.session = null;
    this.isSignedIn = false;
    this.listener?.();
  }
}
