import Sidebar from "@/components/Sidebar";

export default async function DashboardLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  return (
    <div className="flex min-h-screen">
      <Sidebar sessionId={sessionId} />
      <main className="min-w-0 flex-1 px-8 py-8">{children}</main>
    </div>
  );
}
