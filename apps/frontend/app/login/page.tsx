"use client";

import Image from "next/image";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/use-auth";

export default function LoginPage() {
  const { isAuthenticated, isLoading, signIn } = useAuth();
  const router = useRouter();

  // Redirect already-authenticated users directly to chat
  useEffect(() => {
    if (isAuthenticated) {
      router.replace("/chat");
    }
  }, [isAuthenticated, router]);

  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    await signIn();
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-white to-gray-50 px-4">
      <div className="w-full max-w-md">
        {/* Calabrio Logo */}
        <div className="flex justify-center mb-12">
          <Image
            src="/images/calabrio-logo.webp"
            alt="Calabrio Logo"
            width={180}
            height={60}
            priority
            className="h-auto w-auto"
          />
        </div>

        {/* Login Card */}
        <div className="rounded-lg bg-white shadow-lg p-8">
          <h1 className="text-2xl font-bold text-gray-900 mb-2 text-center">
            Welcome to Calabrio WFM
          </h1>
          <p className="text-gray-600 text-center mb-8 text-sm">
            Sign in with your Microsoft account to continue
          </p>

          <form onSubmit={handleSignIn} className="space-y-6">
            <button
              type="submit"
              disabled={isLoading}
              className="w-full bg-[var(--calabrio-blue)] hover:bg-[var(--calabrio-blue-dark)] disabled:bg-gray-400 text-white font-semibold py-3 px-4 rounded-lg transition duration-200 flex items-center justify-center gap-3"
            >
              {/* Microsoft logo mark */}
              <svg width="20" height="20" viewBox="0 0 21 21" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <rect x="1" y="1" width="9" height="9" fill="#F25022"/>
                <rect x="11" y="1" width="9" height="9" fill="#7FBA00"/>
                <rect x="1" y="11" width="9" height="9" fill="#00A4EF"/>
                <rect x="11" y="11" width="9" height="9" fill="#FFB900"/>
              </svg>
              {isLoading ? "Redirecting..." : "Sign in with Microsoft"}
            </button>
          </form>

          <p className="text-center text-xs text-gray-500 mt-6">
            Calabrio Workforce Management System
          </p>
        </div>
      </div>
    </div>
  );
}
