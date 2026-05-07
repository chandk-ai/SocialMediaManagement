/**
 * NextAuth options — Okta provider.
 * Set OKTA_ISSUER, OKTA_CLIENT_ID, OKTA_CLIENT_SECRET, NEXTAUTH_SECRET.
 */
import type { NextAuthOptions } from 'next-auth';
import OktaProvider from 'next-auth/providers/okta';

export const authOptions: NextAuthOptions = {
  providers: [
    OktaProvider({
      clientId: process.env.OKTA_CLIENT_ID!,
      clientSecret: process.env.OKTA_CLIENT_SECRET!,
      issuer: process.env.OKTA_ISSUER!,
      authorization: { params: { scope: 'openid profile email offline_access' } },
    }),
  ],
  session: { strategy: 'jwt', maxAge: 60 * 60 * 8 },
  callbacks: {
    async jwt({ token, account, profile }) {
      if (account) {
        token.accessToken = account.access_token;
        token.idToken = account.id_token;
        token.expiresAt = account.expires_at;
      }
      if (profile) {
        // Map custom claims onto the session
        // @ts-expect-error -- custom claim
        token.role = profile['role'] || 'viewer';
        // @ts-expect-error -- custom claim
        token.orgId = profile['org_id'];
      }
      return token;
    },
    async session({ session, token }) {
      // expose only what the UI needs
      (session as any).accessToken = token.accessToken;
      (session as any).role = token.role;
      (session as any).orgId = token.orgId;
      return session;
    },
  },
  pages: { signIn: '/login' },
};
