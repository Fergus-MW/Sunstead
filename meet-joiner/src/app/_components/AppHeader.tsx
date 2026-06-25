import Link from "next/link";

export type NavLink = { href: string; label: string };

// Shared top bar for the admin surfaces (graph, dashboard) so they read as one
// product. `status` is an optional node rendered beside the wordmark (e.g. a
// connection badge); `nav` is the right-aligned link set.
export default function AppHeader({
  title,
  subtitle,
  status,
  nav,
}: {
  title: string;
  subtitle?: string;
  status?: React.ReactNode;
  nav: NavLink[];
}) {
  return (
    <header className="flex items-center justify-between border-b border-[#f3ead3]/10 px-5 py-3">
      <div className="flex items-baseline gap-3">
        <h1 className="font-serif text-lg tracking-tight">{title}</h1>
        {subtitle && (
          <span className="text-[10px] uppercase tracking-[0.35em] text-[#f3ead3]/40">
            {subtitle}
          </span>
        )}
        {status}
      </div>
      <nav className="flex gap-4 text-xs text-[#f3ead3]/60">
        {nav.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className="underline-offset-4 hover:text-[#f3ead3] hover:underline"
          >
            {l.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
