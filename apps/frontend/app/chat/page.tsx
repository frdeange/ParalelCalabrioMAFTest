"use client";

import { AuthGuard } from "@/components/auth/AuthGuard";
import { Chat } from "@/components/Chat";
import { useAuth } from "@/lib/use-auth";

export default function ChatPage() {
  const { account, signOut } = useAuth();

  return (
    <AuthGuard>
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
              onClick={signOut}
              className="mt-2 text-gray-400 hover:text-white transition-colors text-xs"
            >
              Sign out
            </button>
          </div>
        </aside>

        {/* Main chat area — CopilotKit AG-UI integration */}
        <main className="flex-1 flex flex-col">
          <Chat />
        </main>
      </div>
    </AuthGuard>
  );
}
