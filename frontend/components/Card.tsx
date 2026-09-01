export default function Card({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-edge bg-surface p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-medium text-ink">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}
