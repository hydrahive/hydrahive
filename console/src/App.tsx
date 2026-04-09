import { Suspense, lazy, useEffect, useState } from "react";
import { Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { CapabilitiesProvider } from "@/hooks/useCapabilities";
import { ErrorBoundary } from "@/components/ErrorBoundary";

const AdminLayout       = lazy(() => import("@/components/layout/AdminLayout").then((m) => ({ default: m.AdminLayout })));
const LoginPage         = lazy(() => import("@/pages/LoginPage").then((m) => ({ default: m.LoginPage })));
const SetupPage         = lazy(() => import("@/pages/SetupPage").then((m) => ({ default: m.SetupPage })));
const DashboardPage     = lazy(() => import("@/pages/DashboardPage").then((m) => ({ default: m.DashboardPage })));
const AgentsPage        = lazy(() => import("@/pages/AgentsPage").then((m) => ({ default: m.AgentsPage })));
const ProjectsPage      = lazy(() => import("@/pages/ProjectsPage").then((m) => ({ default: m.ProjectsPage })));
const ProjectCreatePage = lazy(() => import("@/pages/ProjectCreatePage").then((m) => ({ default: m.ProjectCreatePage })));
const SystemPage        = lazy(() => import("@/pages/SystemPage").then((m) => ({ default: m.SystemPage })));
const ChatPage          = lazy(() => import("@/pages/ChatPage").then((m) => ({ default: m.ChatPage })));
const AgentChatPage     = lazy(() => import("@/pages/AgentChatPage").then((m) => ({ default: m.AgentChatPage })));
const MyAgentPage       = lazy(() => import("@/pages/MyAgentPage").then((m) => ({ default: m.MyAgentPage })));
const SettingsPage      = lazy(() => import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const WizardPage        = lazy(() => import("@/pages/WizardPage").then((m) => ({ default: m.WizardPage })));
const SearchPage        = lazy(() => import("@/pages/SearchPage").then((m) => ({ default: m.SearchPage })));
const CodeEditorPage    = lazy(() => import("@/pages/CodeEditorPage").then((m) => ({ default: m.CodeEditorPage })));
const SchedulesPage     = lazy(() => import("@/pages/SchedulesPage"));
const BlueprintPage     = lazy(() => import("@/pages/BlueprintPage").then((m) => ({ default: m.BlueprintPage })));
const HubPage           = lazy(() => import("@/pages/HubPage").then((m) => ({ default: m.HubPage })));
const HydraBrainPage    = lazy(() => import("@/pages/HydraBrainPage").then((m) => ({ default: m.HydraBrainPage })));
const VoicePage         = lazy(() => import("@/pages/VoicePage").then((m) => ({ default: m.VoicePage })));
const OnboardingWizardPage   = lazy(() => import("@/pages/OnboardingWizardPage").then((m) => ({ default: m.OnboardingWizardPage })));
const InvitePage             = lazy(() => import("@/pages/InvitePage").then((m) => ({ default: m.InvitePage })));
const UserManagementPage     = lazy(() => import("@/pages/UserManagementPage").then((m) => ({ default: m.UserManagementPage })));
const PromptGuidePage        = lazy(() => import("@/pages/PromptGuidePage").then((m) => ({ default: m.PromptGuidePage })));
const McpConfigPage          = lazy(() => import("@/pages/McpConfigPage").then((m) => ({ default: m.McpConfigPage })));
const QuickstartPage         = lazy(() => import("@/pages/QuickstartPage").then((m) => ({ default: m.QuickstartPage })));
const PlaygroundPage         = lazy(() => import("@/pages/PlaygroundPage").then((m) => ({ default: m.PlaygroundPage })));

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />;
}

function OnboardingGuard({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (!isAuthenticated) { setChecked(true); return; }
    if (sessionStorage.getItem("hh_wizard_done")) { setChecked(true); return; }
    const token = localStorage.getItem("hydrahive_token") || "";
    fetch("/api/me/wizard-status", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => r.json())
      .then((d: { done: boolean }) => {
        if (d.done) {
          sessionStorage.setItem("hh_wizard_done", "1");
        } else {
          navigate("/onboarding", { replace: true });
        }
      })
      .catch(e => console.error("Failed to check wizard status", e))
      .finally(() => setChecked(true));
  }, [isAuthenticated, navigate]);

  if (!checked) return null;
  return <>{children}</>;
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
      .catch(e => console.error("Failed to check setup status", e))
      .finally(() => setChecked(true));
  }, [navigate]);

  if (!checked) return null;
  return <>{children}</>;
}

export default function App() {
  return (
    <ErrorBoundary>
    <SetupGuard>
      <Suspense fallback={<div className="min-h-screen bg-background" />}>
        <Routes>
          <Route path="/setup"      element={<SetupPage />} />
          <Route path="/wizard"     element={<WizardPage />} />
          <Route path="/login"      element={<LoginPage />} />
          <Route path="/invite/:token" element={<InvitePage />} />
          <Route path="/onboarding" element={<ProtectedRoute><OnboardingWizardPage /></ProtectedRoute>} />
          <Route path="/" element={<ProtectedRoute><CapabilitiesProvider><OnboardingGuard><AdminLayout /></OnboardingGuard></CapabilitiesProvider></ProtectedRoute>}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard"         element={<DashboardPage />} />
            <Route path="agents"            element={<AgentsPage />} />
            <Route path="activity"          element={<Navigate to="/dashboard?tab=activity" replace />} />
            <Route path="usage"             element={<Navigate to="/dashboard?tab=usage" replace />} />
            <Route path="projects"          element={<ProjectsPage />} />
            <Route path="projects/new"      element={<ProjectCreatePage />} />
            <Route path="chat/:id"          element={<ChatPage />} />
            <Route path="agents/:id/chat"   element={<AgentChatPage />} />
            <Route path="search"            element={<SearchPage />} />
            <Route path="code-editor"       element={<CodeEditorPage />} />
            <Route path="schedules"         element={<SchedulesPage />} />
            <Route path="blueprint"         element={<BlueprintPage />} />
            <Route path="hub"               element={<HubPage />} />
            <Route path="brain"             element={<HydraBrainPage />} />
            <Route path="voice"             element={<VoicePage />} />
            <Route path="system"            element={<SystemPage />} />
            <Route path="audit"             element={<Navigate to="/dashboard?tab=audit" replace />} />
            <Route path="my-agent"          element={<MyAgentPage />} />
            <Route path="settings"          element={<SettingsPage />} />
            <Route path="usermanagement"    element={<UserManagementPage />} />
            <Route path="prompt-guide"      element={<PromptGuidePage />} />
            <Route path="quickstart"        element={<QuickstartPage />} />
            <Route path="playground"       element={<PlaygroundPage />} />
            {/* Redirects für konsolidierte Seiten */}
            <Route path="tools"             element={<Navigate to="/agents?tab=tools" replace />} />
            <Route path="tools/skill-packages" element={<Navigate to="/hub?tab=skill-packages" replace />} />
            <Route path="federation"        element={<Navigate to="/agents?tab=federation" replace />} />
            <Route path="extensions"        element={<Navigate to="/hub?tab=extensions" replace />} />
            <Route path="plugins"           element={<Navigate to="/hub?tab=plugins" replace />} />
            <Route path="secrets"           element={<Navigate to="/usermanagement?tab=secrets" replace />} />
            <Route path="config-hub"        element={<Navigate to="/settings" replace />} />
            <Route path="butler"            element={<Navigate to="/blueprint" replace />} />
            {/* Redirects für alte Bookmarks */}
            <Route path="llm"    element={<Navigate to="/settings" replace />} />
            <Route path="mcp"    element={<McpConfigPage />} />
            <Route path="gitea"  element={<Navigate to="/settings" replace />} />
            <Route path="vpn"    element={<Navigate to="/settings" replace />} />
            <Route path="users"  element={<Navigate to="/settings" replace />} />
            <Route path="backup" element={<Navigate to="/settings" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </SetupGuard>
    </ErrorBoundary>
  );
}
