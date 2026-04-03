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
const ChatPage          = lazy(() => import("@/pages/ChatPage").then((m) => ({ default: m.ChatPage })));
const AgentChatPage     = lazy(() => import("@/pages/AgentChatPage").then((m) => ({ default: m.AgentChatPage })));
const AuditPage         = lazy(() => import("@/pages/AuditPage").then((m) => ({ default: m.AuditPage })));
const MyAgentPage       = lazy(() => import("@/pages/MyAgentPage").then((m) => ({ default: m.MyAgentPage })));
const SettingsPage      = lazy(() => import("@/pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const WizardPage        = lazy(() => import("@/pages/WizardPage").then((m) => ({ default: m.WizardPage })));
const ActivityPage      = lazy(() => import("@/pages/ActivityPage").then((m) => ({ default: m.ActivityPage })));
const UsagePage         = lazy(() => import("@/pages/UsagePage").then((m) => ({ default: m.UsagePage })));
const SearchPage        = lazy(() => import("@/pages/SearchPage").then((m) => ({ default: m.SearchPage })));
const CodeEditorPage    = lazy(() => import("@/pages/CodeEditorPage").then((m) => ({ default: m.CodeEditorPage })));
const ExtensionsPage    = lazy(() => import("@/pages/ExtensionsPage").then((m) => ({ default: m.ExtensionsPage })));
const PluginsPage       = lazy(() => import("@/pages/PluginsPage").then((m) => ({ default: m.PluginsPage })));
const SchedulesPage     = lazy(() => import("@/pages/SchedulesPage"));
const A2APage           = lazy(() => import("@/pages/A2APage").then((m) => ({ default: m.A2APage })));
const ButlerPage        = lazy(() => import("@/pages/ButlerPage").then((m) => ({ default: m.ButlerPage })));
const BlueprintPage     = lazy(() => import("@/pages/BlueprintPage").then((m) => ({ default: m.BlueprintPage })));
const SkillPackagesPage = lazy(() => import("@/pages/SkillPackagesPage").then((m) => ({ default: m.SkillPackagesPage })));
const HubPage                = lazy(() => import("@/pages/HubPage").then((m) => ({ default: m.HubPage })));
const SecretsPage            = lazy(() => import("@/pages/SecretsPage").then((m) => ({ default: m.SecretsPage })));
const HydraBrainPage         = lazy(() => import("@/pages/HydraBrainPage").then((m) => ({ default: m.HydraBrainPage })));
const VoicePage              = lazy(() => import("@/pages/VoicePage").then((m) => ({ default: m.VoicePage })));
const OnboardingWizardPage   = lazy(() => import("@/pages/OnboardingWizardPage").then((m) => ({ default: m.OnboardingWizardPage })));
const InvitePage             = lazy(() => import("@/pages/InvitePage").then((m) => ({ default: m.InvitePage })));
const ConfigHubPage          = lazy(() => import("@/pages/ConfigHubPage").then((m) => ({ default: m.ConfigHubPage })));
const UserManagementPage     = lazy(() => import("@/pages/UserManagementPage").then((m) => ({ default: m.UserManagementPage })));
const PromptGuidePage        = lazy(() => import("@/pages/PromptGuidePage").then((m) => ({ default: m.PromptGuidePage })));

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
      .catch(() => {})
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
          <Route path="/setup"      element={<SetupPage />} />
          <Route path="/wizard"     element={<WizardPage />} />
          <Route path="/login"      element={<LoginPage />} />
          <Route path="/invite/:token" element={<InvitePage />} />
          <Route path="/onboarding" element={<ProtectedRoute><OnboardingWizardPage /></ProtectedRoute>} />
          <Route path="/" element={<ProtectedRoute><OnboardingGuard><AdminLayout /></OnboardingGuard></ProtectedRoute>}>
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard"         element={<DashboardPage />} />
            <Route path="agents"            element={<AgentsPage />} />
            <Route path="activity"          element={<ActivityPage />} />
            <Route path="usage"             element={<UsagePage />} />
            <Route path="projects"          element={<ProjectsPage />} />
            <Route path="projects/new"      element={<ProjectCreatePage />} />
            <Route path="chat/:id"          element={<ChatPage />} />
            <Route path="agents/:id/chat"   element={<AgentChatPage />} />
            <Route path="search"            element={<SearchPage />} />
            <Route path="code-editor"       element={<CodeEditorPage />} />
            <Route path="extensions"        element={<ExtensionsPage />} />
            <Route path="plugins"           element={<PluginsPage />} />
            <Route path="schedules"         element={<SchedulesPage />} />
            <Route path="federation"        element={<A2APage />} />
            <Route path="blueprint"         element={<BlueprintPage />} />
            <Route path="butler"           element={<Navigate to="/blueprint" replace />} />
            <Route path="tools/skill-packages" element={<SkillPackagesPage />} />
            <Route path="hub"                  element={<HubPage />} />
            <Route path="secrets"           element={<SecretsPage />} />
            <Route path="brain"             element={<HydraBrainPage />} />
            <Route path="voice"             element={<VoicePage />} />
            <Route path="system"            element={<SystemPage />} />
            <Route path="tools"             element={<ToolsPage />} />
            <Route path="audit"             element={<AuditPage />} />
            <Route path="my-agent"          element={<MyAgentPage />} />
            <Route path="settings"          element={<SettingsPage />} />
            <Route path="config-hub"       element={<ConfigHubPage />} />
            <Route path="usermanagement"  element={<UserManagementPage />} />
            <Route path="prompt-guide"    element={<PromptGuidePage />} />
            {/* Redirects für alte Bookmarks */}
            <Route path="llm"    element={<Navigate to="/settings" replace />} />
            <Route path="mcp"    element={<Navigate to="/settings" replace />} />
            <Route path="gitea"  element={<Navigate to="/settings" replace />} />
            <Route path="vpn"    element={<Navigate to="/settings" replace />} />
            <Route path="users"  element={<Navigate to="/settings" replace />} />
            <Route path="backup" element={<Navigate to="/settings" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </SetupGuard>
  );
}
