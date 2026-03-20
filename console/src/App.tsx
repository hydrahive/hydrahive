import { Routes, Route, Navigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { AdminLayout } from "@/components/layout/AdminLayout";
import { LoginPage } from "@/pages/LoginPage";
import { DashboardPage } from "@/pages/DashboardPage";
import { AgentsPage } from "@/pages/AgentsPage";
import { ProjectsPage } from "@/pages/ProjectsPage";
import { ProjectCreatePage } from "@/pages/ProjectCreatePage";
import { SystemPage } from "@/pages/SystemPage";
import { ToolsPage } from "@/pages/ToolsPage";
import { LlmConfigPage } from "@/pages/LlmConfigPage";
import { UserPage } from "@/pages/UserPage";
import { ChatPage } from "@/pages/ChatPage";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Routes>
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
      </Route>
    </Routes>
  );
}
