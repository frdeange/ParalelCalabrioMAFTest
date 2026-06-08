"use client";

import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { useMsal } from "@azure/msal-react";

export default function ChatPage() {
  const { accounts, instance } = useMsal();
  const account = accounts[0];

  const handleSignOut = () => {
    instance.logoutRedirect({ postLogoutRedirectUri: "/" });
  };

  return (
    <ProtectedRoute>
      <div className="flex min-h-screen bg-gray-50">
        {/* Sidebar placeholder — full implementation in issue #27 */}
        <aside className="w-64 bg-[#1a1f36] text-white flex flex-col">
          <div className="p-4 border-b border-white/10">
            {/* Calabrio logo in sidebar */}
            <img
              src="/images/calabrio-logo.webp"
              alt="Calabrio"
              className="h-8 w-auto brightness-0 invert"
            />
          </div>
          <nav className="flex-1 overflow-y-auto py-4">
            {[
              "People",
              "Permissions",
              "Plans",
              "Shift bidding",
              "Schedules",
              "Sessions",
              "Requests",
              "Intraday",
              "Adherence",
              "Partner Manager",
              "Reports",
              "Payroll Integration",
              "WFM Settings",
              "Meetings",
              "MyTime",
            ].map((item) => (
              <button
                key={item}
                className="w-full text-left px-4 py-2 text-sm text-gray-300 hover:bg-white/10 hover:text-white transition-colors"
              >
                {item}
              </button>
            ))}
          </nav>
          {/* User info + sign-out */}
          <div className="p-4 border-t border-white/10 text-sm">
            <p className="text-gray-400 truncate">{account?.username ?? ""}</p>
            <button
              onClick={handleSignOut}
              className="mt-2 text-gray-400 hover:text-white transition-colors text-xs"
            >
              Sign out
            </button>
          </div>
        </aside>

        {/* Main chat area placeholder — CopilotKit wired in issue #27 */}
        <main className="flex-1 flex flex-col items-center justify-center p-8">
          <div className="max-w-2xl w-full text-center space-y-4">
            <h1 className="text-2xl font-bold text-gray-900">
              Supervisor Assist Chat
            </h1>
            <p className="text-gray-600">
              Signed in as{" "}
              <span className="font-medium">{account?.name ?? account?.username}</span>
            </p>
            <p className="text-gray-400 text-sm">
              Chat interface will be wired with CopilotKit in issue #27.
            </p>
          </div>
        </main>
      </div>
    </ProtectedRoute>
  );
}
