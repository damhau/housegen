import { Link, useNavigate, useRouterState } from "@tanstack/react-router"
import { useQueryClient } from "@tanstack/react-query"
import { Building2, Check, ChevronsUpDown, LogOut, Plus, Settings, ShieldCheck } from "lucide-react"
import { getMeQueryKey, useMe, useSwitchOrganisation } from "@/api/endpoints/accounts/accounts"
import type { MeOut, OrganisationOut } from "@/api/model"
import { Menu, MenuItem, MenuLabel, MenuSeparator, menuItemClass } from "@/components/ui/menu"
import { PROVIDER_LABEL, refreshSession, SIGN_OUT_URL } from "@/lib/auth"
import { errorMessage } from "@/lib/utils"
import { Avatar } from "./Avatar"

/**
 * The header's organisation switcher and user menu, for a signed-in user. While auth is disabled
 * there is neither, only the link to the admin view (the local user is the administrator).
 */
export function AccountMenu() {
  const me = useMe()
  if (!me.data || !me.data.user) return null
  if (me.data.auth.disabled)
    return me.data.is_admin ? (
      <Link to="/admin" className="flex items-center gap-1.5 rounded-md px-2 py-1 text-sm hover:bg-accent">
        <ShieldCheck className="size-4 text-muted-foreground" />
        Admin
      </Link>
    ) : null
  return (
    <div className="flex items-center gap-1">
      {me.data.organisation && <OrganisationMenu me={me.data} current={me.data.organisation} />}
      <UserMenu me={me.data} />
    </div>
  )
}

function OrganisationMenu({ me, current }: { me: MeOut; current: OrganisationOut }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const pathname = useRouterState({ select: (s) => s.location.pathname })
  const switchOrganisation = useSwitchOrganisation()

  async function choose(id: string) {
    try {
      const next = await switchOrganisation.mutateAsync({ data: { organisation_id: id } })
      qc.setQueryData(getMeQueryKey(), next)
      // a project page belongs to the organisation just left
      if (pathname.startsWith("/projects/")) await navigate({ to: "/" })
    } catch (e) {
      window.alert(errorMessage(e))
    }
    await refreshSession(qc)
  }

  return (
    <Menu
      label="Organisation"
      button={
        <>
          <Building2 className="size-4 text-muted-foreground" />
          <span className="max-w-32 truncate font-medium sm:max-w-48">{current.name}</span>
          <ChevronsUpDown className="size-3.5 text-muted-foreground" />
        </>
      }
    >
      {(close) => (
        <>
          {me.organisations.length > 1 && (
            <>
              <MenuLabel>Switch organisation</MenuLabel>
              {me.organisations.map((o) => (
                <MenuItem
                  key={o.id}
                  disabled={switchOrganisation.isPending}
                  onClick={() => {
                    close()
                    if (o.id !== current.id) void choose(o.id)
                  }}
                >
                  <Check className={o.id === current.id ? "" : "invisible"} />
                  <span className="min-w-0 flex-1 truncate">{o.name}</span>
                  <span className="text-xs text-muted-foreground">{o.role}</span>
                </MenuItem>
              ))}
              <MenuSeparator />
            </>
          )}
          <Link to="/organisation" role="menuitem" className={menuItemClass} onClick={close}>
            <Settings />
            Organisation settings
          </Link>
          <Link to="/welcome" role="menuitem" className={menuItemClass} onClick={close}>
            <Plus />
            Create or join another
          </Link>
        </>
      )}
    </Menu>
  )
}

function UserMenu({ me }: { me: MeOut }) {
  const user = me.user!
  return (
    <Menu label="Account" className="px-1" button={<Avatar name={user.name} email={user.email} picture={user.picture} />}>
      {(close) => (
        <>
          <div className="flex items-center gap-3 px-2 py-2">
            <Avatar name={user.name} email={user.email} picture={user.picture} className="size-9" />
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">{user.name || user.email}</div>
              {user.email && <div className="truncate text-xs text-muted-foreground">{user.email}</div>}
              <div className="text-xs text-muted-foreground">via {PROVIDER_LABEL[user.provider] ?? user.provider}</div>
            </div>
          </div>
          <MenuSeparator />
          {me.is_admin && (
            <Link to="/admin" role="menuitem" className={menuItemClass} onClick={close}>
              <ShieldCheck />
              Admin
            </Link>
          )}
          <a href={SIGN_OUT_URL} role="menuitem" className={menuItemClass}>
            <LogOut />
            Sign out
          </a>
        </>
      )}
    </Menu>
  )
}
