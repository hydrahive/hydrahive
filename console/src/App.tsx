import { useEffect, useState } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { LoginPage } from "@/pages/LoginPage";
import { SetupPage } from "@/pages/SetupPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { AgentsPage } from "@/pages/AgentsPage";
import { ProjectsPage } from "@/pages/ProjectsPage";
import { ProjectCreatePage } from "@/pages/ProjectCreatePage";
import { SystemPage } from "@/pages/SystemPage";
import { ToolsPage } from "@/pages/ToolsPage";
import { LlmConfigPage } from "@/pages/LlmConfigPage";
import { UserPage } from "@/pages/UserPage";
import { ChatPage } from "@/pages/ChatPage";
import { AuditPage } from "@/pages/AuditPage";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />;
}

/** Leitet beim ersten Öffnen auf /setup wenn noch kein User existiert. */
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
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<ProtectedRoute><AdminLayout /></ProtectedRoute>}>
          <Route index element={<Navigate to="/dashboard" replace />} />
          <Route path="dashboard"         element={<DashboardPage />} />
          <Route path="agents"            element={<AgentsPage />} />
          <Route path="projects"          element={<ProjectsPage />} />
          <Route path="projects/new"      element={<ProjectCreatePage />} />
          <Route path="chat/:id"          element={<ChatPage />} />
          <Route path="system"            element={<SystemPage />} />
          <Route path="tools"             element={<ToolsPage />} />
          <Route path="llm"              element={<LlmConfigPage />} />
          <Route path="users"            element={<UserPage />} />
          <Route path="audit"            element={<AuditPage />} />
        </Route>
      </Routes>
    </SetupGuard>
  );
}
