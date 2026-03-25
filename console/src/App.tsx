import { Suspense, lazy, useEffect, useState } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";

const AdminLayout       = lazy(() => import("@/components/layout/AdminLayout").then((m) => ({ default: m.AdminLayout })));
const LoginPage         = lazy(() => import("@/pages/LoginPage").then((m) => ({ default: m.LoginPage })));
const SetupPage         = lazy(() => import("@/pages/SetupPage").then((m) => ({ default: m.SetupPage })));
const DashboardPage     = lazy(() => import("@/pages/DashboardPage").then((m) => ({ default: m.DashboardPage })));
const AgentsPage        = lazy(() => import("@/pages/AgentsPage").then((m) => ({ default: m.AgentsPage })));
const ProjectsPage      = lazy(() => import("@/pages/ProjectsPage").then((m) => ({ default: m.ProjectsPage })));
const ProjectCreatePage = lazy(() => import("@/pages/ProjectCreatePage").then((m) => ({ default: m.ProjectCreatePage })));
const SystemPage        = lazy(() => import("@/pages/SystemPage").then((m) => ({ default: m.SystemPage })));
const ToolsPage         = lazy(() => import("@/pages/ToolsPage").then((m) => ({ default: m.ToolsPage })));
const UserPage          = lazy(() => import("@/pages/UserPage").then((m) => ({ default: m.UserPage })));
const ChatPage          = lazy(() => import("@/pages/ChatPage").then((m) => ({ default: m.ChatPage })));
const AgentChatPage     = lazy(() => import("@/pages/AgentChatPage").then((m) => ({ default: m.AgentChatPage })));
const AuditPage         = lazy(() => import("@/pages/AuditPage").then((m) => ({ default: m.AuditPage })));
const BackupPage        = lazy(() => import("@/pages/BackupPage").then((m) => ({ default: m.BackupPage })));
const MyAgentPage       = lazy(() => import("@/pages/MyAgentPage").then((m) => ({ default: m.MyAgentPage })));
const SettingsPage      = lazy(() => import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const WizardPage        = lazy(() => import("@/pages/WizardPage").then((m) => ({ default: m.WizardPage })));
const ActivityPage      = lazy(() => import("@/pages/ActivityPage").then((m) => ({ default: m.ActivityPage })));

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />;
}

function SetupGuard({ children }: { children: React.ReactNode }) {
  const [checked, setChecked] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    fetch("/api/setup/status")
      .then(r => r.json())
      .then((d: { needs_setup: boolean }) => {
        if (d.needs_setup) navigate("/setup", { replace: true });
      })
      .catch(() => {})
      .finally(() => setChecked(true));
  }, [navigate]);

  if (!checked) return null;
  return <>{children}</>;
}

export default function App() {
  return (
    <SetupGuard>
      <Suspense fallback={<div className="min-h-screen bg-background" />}>
        <Routes>
          <Route path="/setup"  element={<SetupPage />} />
          <Route path="/wizard" element={<WizardPage />} />
          <Route path="/login"  element={<LoginPage />} />
          <Route path="/" element={<ProtectedRoute><AdminLayout /></ProtectedRoute>}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard"         element={<DashboardPage />} />
            <Route path="agents"            element={<AgentsPage />} />
            <Route path="activity"          element={<ActivityPage />} />
            <Route path="projects"          element={<ProjectsPage />} />
            <Route path="projects/new"      element={<ProjectCreatePage />} />
            <Route path="chat/:id"          element={<ChatPage />} />
            <Route path="agents/:id/chat"   element={<AgentChatPage />} />
            <Route path="system"            element={<SystemPage />} />
            <Route path="tools"             element={<ToolsPage />} />
            <Route path="users"             element={<UserPage />} />
            <Route path="audit"             element={<AuditPage />} />
            <Route path="backup"            element={<BackupPage />} />
            <Route path="my-agent"          element={<MyAgentPage />} />
            <Route path="settings"          element={<SettingsPage />} />
            {/* Redirects für alte Bookmarks */}
            <Route path="llm"   element={<Navigate to="/settings" replace />} />
            <Route path="mcp"   element={<Navigate to="/settings" replace />} />
            <Route path="gitea" element={<Navigate to="/settings" replace />} />
            <Route path="vpn"   element={<Navigate to="/settings" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </SetupGuard>
  );
}
