import { Outlet, NavLink, useNavigate } from "react-router-dom";
import { LayoutDashboard, Bot, FolderKanban, Server, LogOut } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
const nav = [
  { to:"/dashboard", icon:LayoutDashboard, label:"Dashboard" },
  { to:"/agents",    icon:Bot,             label:"Agenten"   },
  { to:"/projects",  icon:FolderKanban,    label:"Projekte"  },
  { to:"/system",    icon:Server,          label:"System"    },
];
export function AdminLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="flex h-screen bg-background">
      <aside className="w-56 flex flex-col border-r bg-card">
        <div className="h-14 flex items-center gap-2 px-4 border-b">
          <div className="w-7 h-7 rounded-md bg-primary flex items-center justify-center">
            <span className="text-primary-foreground font-bold text-sm">O</span>
          </div>
          <span className="font-semibold text-sm">OctopOS</span>
        </div>
        <nav className="flex-1 p-2 space-y-0.5">
          {nav.map(({to, icon:Icon, label}) => (
            <NavLink key={to} to={to} className={({isActive}) =>
              cn("flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                isActive ? "bg-primary/10 text-primary font-medium" : "text-muted-foreground hover:bg-accent hover:text-accent-foreground")}>
              <Icon className="h-4 w-4" />{label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t space-y-1">
          <div className="px-3 py-1.5 text-xs text-muted-foreground truncate">{user?.username}</div>
          <button onClick={() => { logout(); navigate("/login"); }}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors">
            <LogOut className="h-4 w-4" />Abmelden
          </button>
        </div>
      </aside>
      <main className="flex-1 overflow-auto"><Outlet /></main>
    </div>
  );
}
