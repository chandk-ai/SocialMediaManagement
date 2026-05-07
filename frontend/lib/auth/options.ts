/**
 * NextAuth options — Okta provider, conditionally enabled.
 *
 * Okta is opt-in. If OKTA_ISSUER is set we register the provider; otherwise
 * NextAuth runs with no providers and the frontend falls back to Supabase
 * Auth directly via @supabase/ssr (the recommended path).
 */
import type { NextAuthOptions } from 'next-auth';
import type { Provider } from 'next-auth/providers/index';
import OktaProvider from 'next-auth/providers/okta';

const providers: Provider[] = [];
if (process.env.OKTA_ISSUER && process.env.OKTA_CLIENT_ID && process.env.OKTA_CLIENT_SECRET) {
  providers.push(
    OktaProvider({
      clientId: process.env.OKTA_CLIENT_ID,
      clientSecret: process.env.OKTA_CLIENT_SECRET,
      issuer: process.env.OKTA_ISSUER,
      authorization: { params: { scope: 'openid profile email offline_access' } },
    }),
  );
}

export const authOptions: NextAuthOptions = {
  providers,
  session: { strategy: 'jwt', maxAge: 60 * 60 * 8 },
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account) {
        token.accessToken = account.access_token;
        token.idToken = account.id_token;
        token.expiresAt = account.expires_at;
      }
      if (profile) {
        // @ts-expect-error -- custom claim
        token.role = profile['role'] || 'viewer';
        // @ts-expect-error -- custom claim
        token.orgId = profile['org_id'];
      }
      return token;
    },
    async session({ session, token }) {
      (session as any).accessToken = token.accessToken;
      (session as any).role = token.role;
      (session as any).orgId = token.orgId;
      return session;
    },
  },
  pages: { signIn: '/login' },
};
