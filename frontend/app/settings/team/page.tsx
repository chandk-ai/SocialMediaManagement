'use client';
/**
 * Team management — members + pending invitations.
 *
 * Email-claim model: admins enter an email + role here. Nothing is sent.
 * The admin shares the app URL out-of-band ("hey, sign in at <url>"); when
 * the invitee signs in (with that email, via OAuth or magic-link), the
 * backend matches by email and joins them to the org at the chosen role.
 *
 * Role rules surfaced in the UI:
 *   - Only admins can invite, change roles, or remove members.
 *   - You can't demote your own admin role (block self-foot-guns).
 *   - You can't remove yourself (same reason).
 *   - The last admin can't be removed (the backend enforces this too).
 */
import { useState } from 'react';
import { mutate } from 'swr';
import { Card, CardTitle, CardDescription } from '@/components/ui/Card';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Badge } from '@/components/ui/Badge';
import { ApiError, api, useApi } from '@/lib/api/client';
import {
  AlertTriangle, CheckCircle2, Copy, Loader2, Mail, ShieldCheck,
  Trash2, UserPlus, Users,
} from 'lucide-react';
import { formatDateTime } from '@/lib/utils';

type Role = 'viewer' | 'editor' | 'admin';

type Member = {
  user_id: string;
  email: string;
  display_name: string;
  role: Role;
  is_active: boolean;
  created_at: string;
  claimed: boolean;
};

type Invitation = {
  id: string;
  email: string;
  role: Role;
  invited_by: string | null;
  invited_at: string;
};

type MembersResp = { members: Member[] };
type InvitesResp = { invitations: Invitation[] };
type MeResp = { subject: string; email: string; name: string; org_id: string; role: Role };

export default function TeamSettingsPage() {
  // /auth/me returns the current Principal so we can gate the Admin-only
  // controls. The backend re-checks authorization on every write — this
  // is purely UI courtesy.
  const { data: me } = useApi<MeResp>('/auth/me');
  const { data: membersData } = useApi<MembersResp>('/team/members');
  const { data: invitesData } = useApi<InvitesResp>('/team/invitations');

  const members = membersData?.members ?? [];
  const invitations = invitesData?.invitations ?? [];
  const isAdmin = me?.role === 'admin';
  const myEmail = me?.email?.toLowerCase() ?? null;

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Team" />
        <main className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-6 max-w-4xl">
          {/* Invite new */}
          <Card>
            <div className="flex items-start gap-3">
              <div className="size-9 rounded-lg bg-accent-muted flex items-center justify-center text-accent shrink-0">
                <UserPlus size={18} />
              </div>
              <div className="flex-1">
                <CardTitle>Invite a teammate</CardTitle>
                <CardDescription>
                  Add their email and choose a role. They sign in at this app
                  with the same email — OAuth, Okta, or magic link — and the
                  system joins them to your workspace automatically. No email
                  is sent; share the app URL with them however you want
                  (Slack, message, in person).
                </CardDescription>
              </div>
            </div>
            <InviteForm disabled={!isAdmin} />
          </Card>

          {/* Pending invitations */}
          <Card>
            <CardTitle className="flex items-center gap-2">
              <Mail size={16} /> Pending invitations
              <Badge tone="default">{invitations.length}</Badge>
            </CardTitle>
            <CardDescription>
              Waiting for sign-in. Auto-claimed the first time the invitee
              authenticates with the email below.
            </CardDescription>
            <div className="mt-3 divide-y divide-ink-200">
              {invitations.length === 0 && (
                <p className="text-sm text-ink-500 py-3">No pending invitations.</p>
              )}
              {invitations.map(inv => (
                <InvitationRow key={inv.id} inv={inv} canManage={isAdmin} />
              ))}
            </div>
          </Card>

          {/* Members */}
          <Card>
            <CardTitle className="flex items-center gap-2">
              <Users size={16} /> Members
              <Badge tone="default">{members.length}</Badge>
            </CardTitle>
            <CardDescription>
              Everyone with access to this workspace. Role changes take effect
              on the next request.
            </CardDescription>
            <div className="mt-3 divide-y divide-ink-200">
              {members.length === 0 && (
                <p className="text-sm text-ink-500 py-3">No members yet.</p>
              )}
              {members.map(m => (
                <MemberRow
                  key={m.user_id}
                  member={m}
                  canManage={isAdmin}
                  isSelf={myEmail !== null && m.email.toLowerCase() === myEmail}
                />
              ))}
            </div>
          </Card>
        </main>
      </div>
    </div>
  );
}

// ── Invite form ────────────────────────────────────────────────────────
function InviteForm({ disabled }: { disabled: boolean }) {
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<Role>('editor');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null); setOk(null);
    const trimmed = email.trim();
    if (!trimmed.includes('@')) {
      setErr('Enter a valid email address.');
      setBusy(false);
      return;
    }
    try {
      const resp = await api.post<{ instruction: string; email: string }>(
        '/team/invitations',
        { email: trimmed, role },
      );
      setOk(resp.instruction || `Invited ${resp.email}.`);
      setEmail('');
      await Promise.all([
        mutate('/team/invitations'),
        mutate('/team/members'),
      ]);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Invite failed.');
    } finally { setBusy(false); }
  }

  return (
    <form onSubmit={submit} className="mt-4 space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex-1 min-w-[220px]">
          <label className="block text-[11px] text-ink-500 mb-1">Email</label>
          <Input
            type="email"
            value={email}
            onChange={(e: any) => setEmail(e.target.value)}
            placeholder="alice@company.com"
            disabled={disabled || busy}
          />
        </div>
        <div>
          <label className="block text-[11px] text-ink-500 mb-1">Role</label>
          <select
            className="input min-w-[120px]"
            value={role}
            onChange={e => setRole(e.target.value as Role)}
            disabled={disabled || busy}
          >
            <option value="viewer">Viewer</option>
            <option value="editor">Editor</option>
            <option value="admin">Admin</option>
          </select>
        </div>
        <Button type="submit" disabled={disabled || busy}>
          {busy ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />}
          Send invite
        </Button>
      </div>
      {disabled && (
        <p className="text-[11px] text-ink-500">
          Only admins can invite teammates.
        </p>
      )}
      {err && (
        <div className="flex items-start gap-2 text-[12px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5" /> <span>{err}</span>
        </div>
      )}
      {ok && (
        <div className="flex items-start gap-2 text-[12px] text-emerald-800 bg-emerald-50 border border-emerald-200 rounded p-2">
          <CheckCircle2 size={12} className="mt-0.5" /> <span>{ok}</span>
        </div>
      )}
    </form>
  );
}

// ── Invitation row ─────────────────────────────────────────────────────
function InvitationRow({ inv, canManage }: { inv: Invitation; canManage: boolean }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function revoke() {
    if (!confirm(`Revoke invitation for ${inv.email}?`)) return;
    setBusy(true); setErr(null);
    try {
      await api.del(`/team/invitations/${inv.id}`);
      await mutate('/team/invitations');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Revoke failed.');
    } finally { setBusy(false); }
  }

  async function copyAppUrl() {
    try {
      await navigator.clipboard.writeText(window.location.origin);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* ignore */ }
  }

  return (
    <div className="py-3 flex items-center justify-between gap-3 flex-wrap">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm">
          <Mail size={14} className="text-ink-500" />
          <span className="font-medium truncate">{inv.email}</span>
          <RoleBadge role={inv.role} />
        </div>
        <div className="text-[11px] text-ink-500 mt-0.5">
          Invited {formatDateTime(inv.invited_at)}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={copyAppUrl}>
          {copied ? <CheckCircle2 size={12} /> : <Copy size={12} />}
          {copied ? 'Copied' : 'Copy app URL'}
        </Button>
        {canManage && (
          <Button size="sm" variant="ghost" onClick={revoke} disabled={busy}>
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
            Revoke
          </Button>
        )}
      </div>
      {err && (
        <div className="w-full mt-1 flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5" /> <span>{err}</span>
        </div>
      )}
    </div>
  );
}

// ── Member row ─────────────────────────────────────────────────────────
function MemberRow({ member, canManage, isSelf }: {
  member: Member; canManage: boolean; isSelf: boolean;
}) {
  const [role, setRole] = useState<Role>(member.role);
  const [busy, setBusy] = useState<'role' | 'remove' | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function changeRole(next: Role) {
    setRole(next);
    setBusy('role'); setErr(null);
    try {
      await api.patch(`/team/members/${member.user_id}/role`, { role: next });
      await mutate('/team/members');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Role change failed.');
      setRole(member.role);   // revert dropdown
    } finally { setBusy(null); }
  }

  async function remove() {
    if (!confirm(`Remove ${member.email} from the workspace?`)) return;
    setBusy('remove'); setErr(null);
    try {
      await api.del(`/team/members/${member.user_id}`);
      await mutate('/team/members');
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Remove failed.');
    } finally { setBusy(null); }
  }

  return (
    <div className="py-3 flex items-center justify-between gap-3 flex-wrap">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm">
          <span className="size-7 rounded-full bg-ink-100 flex items-center justify-center text-[11px] font-semibold text-ink-700">
            {(member.display_name || member.email).slice(0, 1).toUpperCase()}
          </span>
          <span className="font-medium truncate">{member.display_name || member.email}</span>
          {isSelf && <Badge tone="info">you</Badge>}
          {!member.claimed && (
            <Badge tone="warning">pending sign-in</Badge>
          )}
        </div>
        <div className="text-[11px] text-ink-500 ml-9 truncate">{member.email}</div>
      </div>
      <div className="flex items-center gap-2">
        {canManage && !isSelf ? (
          <select
            className="input min-w-[110px] text-sm"
            value={role}
            onChange={e => changeRole(e.target.value as Role)}
            disabled={busy !== null}
          >
            <option value="viewer">Viewer</option>
            <option value="editor">Editor</option>
            <option value="admin">Admin</option>
          </select>
        ) : (
          <RoleBadge role={member.role} />
        )}
        {canManage && !isSelf && (
          <Button size="sm" variant="ghost" onClick={remove} disabled={busy !== null}>
            {busy === 'remove' ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
            Remove
          </Button>
        )}
      </div>
      {err && (
        <div className="w-full mt-1 flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
          <AlertTriangle size={12} className="mt-0.5" /> <span>{err}</span>
        </div>
      )}
    </div>
  );
}

function RoleBadge({ role }: { role: Role }) {
  const tone = role === 'admin' ? 'info' : role === 'editor' ? 'success' : 'default';
  return (
    <Badge tone={tone as any}>
      {role === 'admin' && <ShieldCheck size={10} className="inline -mt-0.5 mr-0.5" />}
      {role}
    </Badge>
  );
}
