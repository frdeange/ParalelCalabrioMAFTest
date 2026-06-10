"use client";

import { AuthGuard } from "@/components/auth/AuthGuard";
import { Chat } from "@/components/Chat";
import { BuSelector } from "@/components/BuSelector";
import { useAuth } from "@/lib/use-auth";

export default function ChatPage() {
  const { account, signOut } = useAuth();

  // Friendly first name for the chat greeting ("Hello, <name>!").
  const displayName = account?.name?.split(" ")[0] ?? account?.username?.split("@")[0];

  return (
    <AuthGuard>
      <div className="flex min-h-screen bg-gray-50">
        {/* Sidebar */}
        <aside className="w-64 bg-[var(--calabrio-navy)] text-white flex flex-col">
          <div className="p-5 border-b border-white/10">
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
                className="w-full text-left px-5 py-2 text-sm text-gray-300 hover:bg-white/10 hover:text-white transition-colors"
              >
                {item}
              </button>
            ))}
          </nav>
          {/* User info + sign-out */}
          <div className="p-5 border-t border-white/10 text-sm space-y-3">
            <BuSelector />
            <div>
              <p className="text-gray-400 truncate">{account?.username ?? ""}</p>
              <button
                onClick={signOut}
                className="mt-2 text-gray-400 hover:text-white transition-colors text-xs"
              >
                Sign out
              </button>
            </div>
          </div>
        </aside>

        {/* Main chat area */}
        <main className="flex-1 flex flex-col">
          {/* Top bar with the assistant title */}
          <header className="flex items-center gap-3 border-b border-[var(--calabrio-border)] bg-white px-6 py-4">
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[var(--calabrio-blue)] text-white">
              <svg
                className="h-5 w-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
            </span>
            <div>
              <h1 className="text-base font-semibold text-slate-800">
                Supervisor Assist
              </h1>
              <p className="text-xs text-slate-500">
                AI-powered workforce management assistant
              </p>
            </div>
          </header>
          <div className="flex-1 min-h-0">
            <Chat userName={displayName} />
          </div>
        </main>
      </div>
    </AuthGuard>
  );
}
